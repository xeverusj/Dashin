# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import re
import os
import requests

# ==========================================
# 🌍 DEEPL TRANSLATION SETUP
# ==========================================
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

def translate_title(title):
    """
    Translate non-English title to English using DeepL
    Returns: (translated_text, detected_language)
    """
    if not title or pd.isna(title) or title == "N/A":
        return title, "unknown"

    # Skip if already English (basic check - all ASCII characters)
    if all(ord(char) < 128 for char in str(title)):
        return title, "en"

    # Skip if no API key
    if not DEEPL_API_KEY:
        print("⚠️ DEEPL_API_KEY not set. Skipping translation.")
        return title, "unknown"

    try:
        response = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={
                "auth_key": DEEPL_API_KEY,
                "text": title,
                "target_lang": "EN"
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        translated = data['translations'][0]['text']
        detected_lang = data['translations'][0]['detected_source_language']
        return translated, detected_lang
    except Exception as e:
        print(f"Translation failed for '{title}': {e}")
        return title, "unknown"

# ==========================================
# 🏢 COMPANY CLEANING
# ==========================================
def clean_company_names(df):
    """
    Normalize company names:
    - Remove legal suffixes (GmbH, Ltd, Inc, etc.)
    - Fix casing (ALL CAPS -> Title Case)
    - Remove extra whitespace
    """
    if 'company_name' not in df.columns:
        return df

    def normalize_company(name):
        if pd.isna(name) or name == "N/A":
            return name

        name = str(name).strip()

        # Remove legal suffixes (case insensitive)
        suffixes = [
            r'\s+GmbH$', r'\s+AG$', r'\s+Ltd\.?$', r'\s+Inc\.?$',
            r'\s+LLC$', r'\s+S\.A\.?$', r'\s+B\.V\.?$', r'\s+PLC$',
            r'\s+Limited$', r'\s+Corp\.?$', r'\s+Co\.$', r'\s+S\.r\.l\.?$',
            r'\s+Pty\.?$', r'\s+KG$', r'\s+OHG$', r'\s+GbR$'
        ]

        for suffix in suffixes:
            name = re.sub(suffix, '', name, flags=re.IGNORECASE)

        # Fix casing
        if name.isupper() or name.islower():
            name = name.title()

        # Remove multiple spaces
        name = re.sub(r'\s+', ' ', name)

        return name.strip()

    df['normalized_name'] = df['company_name'].apply(normalize_company)
    return df


def clean_company_column_in_leads(df):
    """
    Clean the company field directly in leads dataframe.

    Supports:
    - 'Company' column in session CSVs
    - 'company' column in merged leads
    """
    if 'Company' not in df.columns and 'company' not in df.columns:
        return df

    # Prefer session column name first
    col_name = 'Company' if 'Company' in df.columns else 'company'

    def normalize_company(name):
        if pd.isna(name) or name == "N/A":
            return name

        name = str(name).strip()

        # Remove legal suffixes and common noise
        suffixes = [
            r'\s+GmbH$', r'\s+AG$', r'\s+Ltd\.?$', r'\s+Inc\.?$',
            r'\s+LLC$', r'\s+S\.A\.?$', r'\s+B\.V\.?$', r'\s+PLC$',
            r'\s+Limited$', r'\s+Corp\.?$', r'\s+Co\.$', r'\s+S\.r\.l\.?$',
            r'\s+Pty\.?$', r'\s+KG$', r'\s+OHG$', r'\s+GbR$',
            r'\s+\(ex\)$'
        ]

        for suffix in suffixes:
            name = re.sub(suffix, '', name, flags=re.IGNORECASE)

        # Fix casing
        if name.isupper() or name.islower():
            name = name.title()

        # Remove multiple spaces
        name = re.sub(r'\s+', ' ', name)

        return name.strip()

    df[col_name] = df[col_name].apply(normalize_company)
    return df

# ==========================================
# 👤 LEAD NAME CLEANING
# ==========================================
def clean_lead_names(df):
    """
    Normalize lead names:
    - Remove titles (Dr., Mr., Prof., etc.)
    - Fix casing (ALL CAPS -> Title Case)
    - Remove extra whitespace
    """
    if 'full_name' not in df.columns:
        return df

    def normalize_name(name):
        if pd.isna(name) or name == "N/A":
            return name

        name = str(name).strip()

        # Remove titles
        titles = [
            r'\bDr\.?\b', r'\bMr\.?\b', r'\bMrs\.?\b', r'\bMs\.?\b',
            r'\bProf\.?\b', r'\bEng\.?\b', r'\bSir\b', r'\bMadam\b'
        ]

        for title in titles:
            name = re.sub(title, '', name, flags=re.IGNORECASE)

        # Fix casing
        if name.isupper():
            name = name.title()

        # Remove multiple spaces
        name = re.sub(r'\s+', ' ', name)

        return name.strip()

    df['full_name'] = df['full_name'].apply(normalize_name)
    return df

# ==========================================
# 🌍 TITLE TRANSLATION
# ==========================================
def translate_titles(df, progress_callback=None):
    """
    Translate foreign job titles to English using DeepL
    Adds columns: title_translated, title_language
    """
    if 'title' not in df.columns:
        return df

    if DEEPL_API_KEY is None:
        print("⚠️ DeepL API key not found. Set DEEPL_API_KEY in .env file.")
        return df

    translated_titles = []
    detected_langs = []
    total = len(df)

    for idx, title in enumerate(df['title']):
        if progress_callback:
            progress_callback(idx + 1, total)

        translated, lang = translate_title(title)
        translated_titles.append(translated)
        detected_langs.append(lang)

    df['title_translated'] = translated_titles
    df['title_language'] = detected_langs

    return df

# ==========================================
# 📧 EMAIL FILTERING
# ==========================================
def remove_personal_emails(df):
    """
    Remove leads with personal email addresses
    Returns: (cleaned_df, removed_count)
    """
    if 'email' not in df.columns:
        return df, 0

    # Personal email domains
    personal_domains = [
        'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
        'aol.com', 'icloud.com', 'live.com', 'msn.com',
        'mail.com', 'gmx.com', 'yandex.com', 'protonmail.com',
        'yahoo.co.uk', 'yahoo.co.in', 'yahoo.fr', 'yahoo.de',
        'googlemail.com', 'me.com', 'mac.com'
    ]

    # Create pattern
    pattern = '|'.join([f'@{domain}$' for domain in personal_domains])

    # Filter out personal emails
    original_count = len(df)
    df_cleaned = df[~df['email'].str.contains(pattern, case=False, na=False, regex=True)]
    removed_count = original_count - len(df_cleaned)

    return df_cleaned, removed_count

# ==========================================
# 🔍 DUPLICATE DETECTION
# ==========================================
def detect_duplicates(df):
    """
    Find duplicate leads based on full_name + company_id
    Returns: DataFrame with duplicates only
    """
    if 'full_name' not in df.columns or 'company_id' not in df.columns:
        return pd.DataFrame()

    # Find duplicates
    duplicates = df[df.duplicated(subset=['full_name', 'company_id'], keep=False)]

    # Sort by name for easier viewing
    if not duplicates.empty:
        duplicates = duplicates.sort_values(['full_name', 'company_id'])

    return duplicates

# ==========================================
# 📊 DATA QUALITY STATS
# ==========================================
def get_data_quality_stats(leads_df, companies_df):
    """
    Calculate data quality statistics
    Returns: dict with quality metrics
    """
    stats = {
        'total_leads': len(leads_df),
        'total_companies': len(companies_df),
        'missing_titles': 0,
        'missing_companies': 0,
        'duplicates': 0,
        'personal_emails': 0
    }

    if not leads_df.empty:
        # Missing titles
        if 'title' in leads_df.columns:
            stats['missing_titles'] = leads_df['title'].isna().sum()

        # Missing companies
        if 'company_id' in leads_df.columns:
            stats['missing_companies'] = leads_df['company_id'].isna().sum()

        # Duplicates
        if 'full_name' in leads_df.columns and 'company_id' in leads_df.columns:
            stats['duplicates'] = leads_df.duplicated(subset=['full_name', 'company_id']).sum()

        # Personal emails
        if 'email' in leads_df.columns:
            personal_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            pattern = '|'.join([f'@{domain}$' for domain in personal_domains])
            stats['personal_emails'] = leads_df['email'].str.contains(pattern, case=False, na=False, regex=True).sum()

    return stats

# ==========================================
# 🔗 MERGE COMPANY NAMES (FIXED)
# ==========================================
def merge_company_names(leads_df, companies_df):
    """
    Merge company names into leads dataframe
    Returns leads_df with 'company' column added
    FIXED: Handles duplicate column names properly
    """
    if leads_df.empty or companies_df.empty:
        return leads_df

    # If 'company' column already exists, return as-is (prevents duplicates)
    if 'company' in leads_df.columns:
        return leads_df

    # Merge on company_id
    if 'company_id' in leads_df.columns and 'company_id' in companies_df.columns:
        # Merge with suffix handling to prevent column name conflicts
        merged = leads_df.merge(
            companies_df[['company_id', 'company_name']],
            on='company_id',
            how='left',
            suffixes=('', '_from_db')  # Prevent duplicate column names
        )

        # Rename company_name to company
        if 'company_name' in merged.columns:
            merged = merged.rename(columns={'company_name': 'company'})

        # Remove any duplicate columns (safety check)
        merged = merged.loc[:, ~merged.columns.duplicated()]

        return merged

    return leads_df

# ==========================================
# 🎯 TITLE ANALYSIS & FILTERING
# ==========================================
def extract_common_titles(df, top_n=50):
    """
    Extract and count the most common job titles
    Returns: DataFrame with title and count
    """
    if 'title' not in df.columns:
        return pd.DataFrame()

    # Clean titles for analysis
    titles = df['title'].dropna()
    titles = titles[titles != "N/A"]
    titles = titles.str.strip()

    # Count occurrences
    title_counts = titles.value_counts().head(top_n)

    return pd.DataFrame({
        'title': title_counts.index,
        'count': title_counts.values
    })

def normalize_title_for_matching(title):
    """
    Normalize title for better matching
    Returns lowercase version with extra spaces removed
    """
    if pd.isna(title) or title == "N/A":
        return ""
    return str(title).lower().strip()

def extract_title_keywords(df):
    """
    Extract common keywords from titles (CEO, Director, Manager, etc.)
    Returns: List of unique keywords sorted by frequency
    """
    if 'title' not in df.columns:
        return []

    titles = df['title'].dropna()
    titles = titles[titles != "N/A"]

    # Common title keywords
    keywords = set()

    for title in titles:
        title_lower = str(title).lower()

        # Extract key words
        words = title_lower.split()
        for word in words:
            # Filter out common words
            if len(word) > 2 and word not in ['and', 'the', 'for', 'of', 'in', 'at', 'to']:
                keywords.add(word.capitalize())

    return sorted(list(keywords))

def filter_by_title(df, include_keywords=None, exclude_keywords=None):
    """
    Filter leads by job title with include/exclude logic
    Args:
        df: DataFrame with leads
        include_keywords: List of keywords to include (e.g., ['CEO', 'Director', 'Head'])
        exclude_keywords: List of keywords to exclude (e.g., ['Sales', 'Marketing'])
    Returns:
        approved_df: Leads that match filters
        rejected_df: Leads that don't match filters
    """
    if 'title' not in df.columns:
        return df.copy(), pd.DataFrame()

    # Default to empty lists
    if include_keywords is None:
        include_keywords = []
    if exclude_keywords is None:
        exclude_keywords = []

    # If no filters, return all as approved
    if not include_keywords and not exclude_keywords:
        return df.copy(), pd.DataFrame()

    def matches_filter(title):
        """
        Check if title matches include/exclude criteria
        Returns: True if approved, False if rejected
        """
        if pd.isna(title) or title == "N/A":
            return False

        title_lower = str(title).lower()

        # STEP 1: Check if title contains any INCLUDE keywords
        if include_keywords:
            has_include = any(keyword.lower() in title_lower for keyword in include_keywords)
            if not has_include:
                return False  # Reject if no include keyword found

        # STEP 2: Check if title contains any EXCLUDE keywords
        if exclude_keywords:
            has_exclude = any(keyword.lower() in title_lower for keyword in exclude_keywords)
            if has_exclude:
                return False  # Reject if exclude keyword found

        # Passed all checks
        return True

    # Apply filter
    mask = df['title'].apply(matches_filter)
    approved = df[mask].copy()
    rejected = df[~mask].copy()

    return approved, rejected

def get_title_statistics(df):
    """
    Get statistics about job titles in the dataset
    Returns: dict with title stats
    """
    if 'title' not in df.columns:
        return {}

    titles = df['title'].dropna()
    titles = titles[titles != "N/A"]

    stats = {
        'total_with_titles': len(titles),
        'unique_titles': titles.nunique(),
        'missing_titles': df['title'].isna().sum(),
        'most_common': titles.value_counts().head(5).to_dict() if len(titles) > 0 else {}
    }

    return stats

# ==========================================
# 🧪 TEST FUNCTIONS (Optional)
# ==========================================
if __name__ == "__main__":
    # Test company cleaning
    test_companies = pd.DataFrame({
        'company_name': ['TESLA INC', 'Siemens GmbH', 'acme corp.', 'Microsoft']
    })
    print("=" * 60)
    print("Company Cleaning Test:")
    print("=" * 60)
    print(clean_company_names(test_companies))
    print()

    # Test leads company cleaning
    test_leads_comp = pd.DataFrame({
        'Company': ['TESLA INC', 'Siemens GmbH (ex)', 'acme corp.  ', 'Microsoft LLC']
    })
    print("=" * 60)
    print("Leads Company Column Cleaning Test:")
    print("=" * 60)
    print(clean_company_column_in_leads(test_leads_comp))
    print()

    # Test name cleaning
    test_leads = pd.DataFrame({
        'full_name': ['DR. JOHN SMITH', 'Mr. Bob Johnson', 'jane doe']
    })
    print("=" * 60)
    print("Name Cleaning Test:")
    print("=" * 60)
    print(clean_lead_names(test_leads))
    print()

    # Test title filtering
    test_titles = pd.DataFrame({
        'title': [
            'CEO',
            'Director of Sales',
            'Technical Director',
            'VP of Marketing',
            'Head of Operations',
            'Sales Manager',
            'CFO'
        ],
        'company_id': ['1', '2', '3', '4', '5', '6', '7']
    })
    test_titles['full_name'] = ['Person ' + str(i) for i in range(1, 8)]

    print("=" * 60)
    print("Title Filtering Test:")
    print("=" * 60)
    print("Original titles:")
    print(test_titles[['full_name', 'title']])
    print()

    # Test: Include "Director" but exclude "Sales"
    approved, rejected = filter_by_title(
        test_titles,
        include_keywords=['Director'],
        exclude_keywords=['Sales']
    )
    print("Filter: Include 'Director', Exclude 'Sales'")
    print(f"\nApproved ({len(approved)}):")
    print(approved[['full_name', 'title']])
    print(f"\nRejected ({len(rejected)}):")
    print(rejected[['full_name', 'title']])
    print()

    # Test translation (will skip if no API key)
    print("=" * 60)
    print("Translation Test:")
    print("=" * 60)
    if DEEPL_API_KEY:
        test_translation = pd.DataFrame({
            'title': ['교사', 'Manager', 'Directeur', 'CEO']
        })
        print(translate_titles(test_translation))
    else:
        print("⚠️ DEEPL_API_KEY not set. Skipping translation test.")

    print("\n✅ All tests completed!")
