# app.py
from flask import Flask, render_template, request
#import requests
import json
import re
from datetime import datetime
import google.generativeai as genai
from urllib.parse import urlencode
import urllib.parse
from tenacity import retry, stop_after_attempt, wait_fixed

# API setup
GEMINI_API_KEY = "AIzaSyAcfuzTMTvGLPEGVYdEU5cVsxPwweT4pkY"  # Replace with your Google Gemini API key
INDIAN_KANOON_API_URL = "https://api.indiankanoon.org/search/"
INDIAN_KANOON_API_TOKEN = "e3c9b71c4904c1aec9e8216553ef0e136c792623"  # Replace with your Indian Kanoon API token
KANOON_HEADERS = {"Authorization": f"Token {INDIAN_KANOON_API_TOKEN}", "Content-Type": "application/json"}

# Configure Gemini
try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    print(f"Error configuring Gemini API: {e}. Will use fallback keyword extraction if needed.")

# Your existing functions (extract_keywords_with_gemini, search_similar_cases, etc.)
def extract_keywords_with_gemini(description):
    prompt = """
    You are a legal expert specializing in Indian law. Extract the main keywords from the following case description for searching legal cases in India. Provide a comma-separated list of up to 5 keywords, focusing on legal terms, acts, and significant entities (e.g., court, property type). Exclude generic words like 'plaintiff' or 'defendant'.

    Case Description: {description}

    Example Output: eviction, Maharashtra Rent Control Act, commercial property, Bombay High Court, lease
    """.format(description=description)
    
    try:
        response = gemini_model.generate_content(prompt)
        keywords = response.text.strip()
        return [kw.strip() for kw in keywords.split(",") if kw.strip()][:5]
    except Exception as e:
        print(f"Error using Gemini API: {e}. Using regex-based keyword extraction.")
        legal_terms = [
            "eviction", "tenant", "landlord", "lease", "contract", "breach",
            "damages", "act", "court", "property", "commercial", "residential",
            "dispute", "reinstatement", "injunction", "copyright", "infringement",
            "software", "intellectual", "financial"
        ]
        words = re.findall(r'\b\w+\b', description.lower())
        keywords = [word for word in words if word in legal_terms or word.istitle()]
        acts = re.findall(r'[A-Z][a-z]+\s*(?:[A-Z][a-z]+\s*)*Act', description)
        keywords.extend(acts)
        return list(dict.fromkeys(keywords))[:5]

@retry(stop=stop_after_attempt(2), wait=wait_fixed(1))
def try_search(query, court, max_results):
    post_data = {"formquery": query, "doctype": "judgment", "maxresults": max_results}
    if court:
        post_data["courts"] = court
    response = requests.post(
        INDIAN_KANOON_API_URL,
        headers={
            "Authorization": f"Token {INDIAN_KANOON_API_TOKEN}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data=urllib.parse.urlencode(post_data),
        timeout=10
    )
    response.raise_for_status()
    return response.json()

def search_similar_cases(keywords, court=None, max_results=5):
    cleaned_keywords = [re.sub(r'[^\w\s]', '', kw).lower() for kw in keywords]
    formatted_keywords = [f'"{kw}"' if " " in kw else kw for kw in cleaned_keywords]
    query = " ".join(formatted_keywords).strip()
    
    try:
        data = try_search(query, court, max_results)
        if "errmsg" in data:
            print(f"[ERROR] API error: {data['errmsg']}")
            simplified_query = " ".join(cleaned_keywords)
            data = try_search(simplified_query, court, max_results)
        return data.get("results", [])
    except Exception as e:
        print(f"Search failed: {e}")
        return []

def summarize_case(case_data):
    return {
        "title": case_data.get("title", "N/A"),
        "court": case_data.get("court", "N/A"),
        "date": case_data.get("date", "N/A"),
        "facts": case_data.get("facts", "N/A")[:200] + "..." if case_data.get("facts") else "N/A",
        "issues": case_data.get("issues", "N/A")[:200] + "..." if case_data.get("issues") else "N/A",
        "decision": case_data.get("decision", "N/A"),
        "plaintiff_won": "plaintiff" in case_data.get("decision", "").lower() and "favor" in case_data.get("decision", "").lower()
    }

def compare_cases(user_case, similar_cases):
    similarities = []
    plaintiff_wins = 0
    total_cases = len(similar_cases)
    
    for case in similar_cases:
        similarity = {
            "title": case["title"],
            "common_issues": [],
            "differences": []
        }
        
        user_issues = user_case["description"].lower()
        case_issues = case["issues"].lower()
        case_facts = case["facts"].lower()
        
        legal_issue_keywords = ["copyright", "lease", "eviction", "contract", "software", "fraud", "negligence", "section", "property"]
        for keyword in legal_issue_keywords:
            if keyword in user_issues and keyword in case_issues:
                similarity["common_issues"].append(keyword.title())
        
        if "commercial" in user_issues and "commercial" not in case_facts:
            similarity["differences"].append("Case does not involve commercial use")
        if "software" in user_issues and "software" not in case_facts:
            similarity["differences"].append("Case does not involve software code")
        if "court" in user_issues and case["court"].lower() not in user_issues:
            similarity["differences"].append(f"Different court: {case['court']}")
            
        similarities.append(similarity)
        if case["plaintiff_won"]:
            plaintiff_wins += 1
    
    win_probability = (plaintiff_wins / total_cases * 100) if total_cases > 0 else 0
    return similarities, win_probability

def generate_judgment(user_case, similarities, win_probability):
    issues = set()
    for sim in similarities:
        issues.update(sim["common_issues"])
    issues_list = ', '.join(sorted(issues)) if issues else 'a general legal dispute'
    judgment = f"Judgment:\nThe present case involves key legal issues including: {issues_list}.\n\n"
    if win_probability >= 75:
        judgment += "The plaintiff has a strong chance of success based on similar precedents and issue alignment.\n\n"
    elif win_probability >= 50:
        judgment += "The plaintiff has a moderate chance of success, though the outcome will depend on the evidence and court interpretation.\n\n"
    else:
        judgment += "There is a low likelihood of success for the plaintiff based on precedent or lack of strong factual alignment.\n\n"
    if user_case.get("court"):
        judgment += f"The case falls under the jurisdiction of the {user_case['court']}, which may influence the outcome based on regional trends.\n\n"
    judgment += "The decision would likely hinge on how well the plaintiff can substantiate the claims and address possible defenses (e.g., fair use, implied consent, procedural gaps)."
    return judgment

def mock_cases(keywords):
    # Include your mock_cases data here (shortened for brevity)
    return [
        {
            "title": "Software Co. vs. Tech Corp (2021)",
            "court": "Delhi High Court",
            "date": "2021-07-10",
            "facts": "Plaintiff claimed defendant copied proprietary software code for commercial use, causing financial loss...",
            "issues": "Copyright infringement, violation of Copyright Act, 1957, software code misuse...",
            "decision": "Ruled in favor of plaintiff; injunction granted, damages awarded."
        },
        # Add more mock cases as needed
    ]

# Flask app setup
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    description = request.form['description']
    court = re.search(r'(?:Bombay|Delhi|Supreme|High)\s*(?:Court)', description, re.IGNORECASE)
    court = court.group(0) if court else None
    
    keywords = extract_keywords_with_gemini(description)
    user_case = {"description": description, "keywords": keywords, "court": court}
    
    cases = search_similar_cases(keywords=keywords, court=court, max_results=5)
    if not cases:
        cases = mock_cases(keywords)
    
    summarized_cases = [summarize_case(case) for case in cases]
    similarities, win_probability = compare_cases(user_case, summarized_cases)
    judgment = generate_judgment(user_case, similarities, win_probability)
    
    return render_template('result.html', description=description, cases=summarized_cases, similarities=similarities, win_probability=win_probability, judgment=judgment)

if __name__ == '__main__':
    app.run(debug=True)