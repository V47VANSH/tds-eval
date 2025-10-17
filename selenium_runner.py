#!/usr/bin/env python
"""
Standalone script to run web tests using Selenium.
This is an alternative to Playwright that avoids asyncio issues on Windows.
"""

import json
import sys
import traceback
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager


def run_selenium_checks(pages_url, checks):
    """Run checks using Selenium WebDriver"""
    results = []
    
    # Set up Chrome options for headless mode
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    try:
        # Initialize the Chrome WebDriver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        
        # Set page load timeout
        driver.set_page_load_timeout(15)
        
        # Navigate to the URL
        driver.get(pages_url)
        
        # Give the page a moment to fully load
        time.sleep(2)
        
        # Run each check
        for check_str in checks:
            passed = False
            details = "Check failed"
            
            try:
                # Page title check
                if check_str.startswith("Page title is"):
                    expected_title = check_str.replace("Page title is ", "").strip("'\"")
                    actual_title = driver.title
                    if actual_title == expected_title:
                        details = f"Page title is correctly '{expected_title}'."
                        passed = True
                    else:
                        details = f"Expected title '{expected_title}', but got '{actual_title}'."
                
                # Element existence check
                elif check_str.startswith("Page contains an element with id"):
                    el_id = check_str.split("'")[1] if "'" in check_str else check_str.split('"')[1]
                    # Remove # from the beginning if present
                    element_id = el_id.lstrip('#')
                    element = WebDriverWait(driver, 5).until(
                        EC.visibility_of_element_located((By.ID, element_id))
                    )
                    details = f"Element with id '{el_id}' is visible."
                    passed = True
                
                # Element text content check
                elif check_str.startswith("The text content of"):
                    parts = check_str.split("'")
                    el_id = parts[1].lstrip('#')  # Remove # if present
                    expected_text = parts[3]
                    element = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.ID, el_id))
                    )
                    actual_text = element.text
                    if actual_text == expected_text:
                        details = f"Element '{el_id}' has correct text: '{expected_text}'."
                        passed = True
                    else:
                        details = f"Expected text '{expected_text}', but got '{actual_text}'."
                
                # Table rows check
                elif "Table has at least" in check_str:
                    count = int(check_str.split(" ")[4])
                    rows = len(driver.find_elements(By.CSS_SELECTOR, "#sales-table tbody tr"))
                    if rows >= count:
                        details = f"Table has {rows} data rows, meeting the requirement."
                        passed = True
                    else:
                        details = f"Expected at least {count} rows, but found {rows}."
                
                # GitHub octocat user check
                elif "After entering 'octocat'" in check_str:
                    driver.find_element(By.ID, "username-input").send_keys("octocat")
                    driver.find_element(By.ID, "fetch-btn").click()
                    creation_date = WebDriverWait(driver, 10).until(
                        EC.text_to_be_present_in_element((By.ID, "creation-date"), "2011-01-25")
                    )
                    details = "GitHub user fetch for 'octocat' was successful."
                    passed = True
                
                # GitHub loading state check
                elif "When fetching user 'octocat'" in check_str:
                    driver.find_element(By.ID, "username-input").clear()
                    driver.find_element(By.ID, "username-input").send_keys("octocat")
                    driver.find_element(By.ID, "fetch-btn").click()
                    
                    # First check for "Loading..." state
                    loading_displayed = WebDriverWait(driver, 2).until(
                        EC.text_to_be_present_in_element((By.ID, "api-status"), "Loading...")
                    )
                    
                    # Then wait for it to become empty
                    WebDriverWait(driver, 10).until(
                        lambda d: d.find_element(By.ID, "api-status").text == ""
                    )
                    details = "#api-status shows 'Loading...' then becomes empty."
                    passed = True
                
                # GitHub error state check
                elif "When fetching a user that does not exist" in check_str:
                    driver.find_element(By.ID, "username-input").clear()
                    driver.find_element(By.ID, "username-input").send_keys("nonexistentuser123456789")
                    driver.find_element(By.ID, "fetch-btn").click()
                    
                    error_shown = WebDriverWait(driver, 10).until(
                        EC.text_to_be_present_in_element((By.ID, "api-status"), "User not found")
                    )
                    details = "#api-status displays 'User not found' for non-existent user."
                    passed = True
                
                # Default case
                else:
                    details = f"Unknown check type: {check_str}"
            
            except Exception as e:
                details = f"Check failed: {str(e)}"
            
            results.append({
                "check": check_str,
                "passed": passed,
                "details": details
            })
    
    except Exception as e:
        # Handle any exception during Selenium setup
        error_message = f"Selenium error: {str(e)}"
        results = [{
            "check": c,
            "passed": False,
            "details": error_message
        } for c in checks]
    
    finally:
        # Make sure we always close the browser
        try:
            driver.quit()
        except:
            pass
    
    return results


def main():
    """Main entry point for the script"""
    if len(sys.argv) != 2:
        print("Usage: python selenium_runner.py <data_file>", file=sys.stderr)
        sys.exit(1)
        
    try:
        # Load data from file
        with open(sys.argv[1], 'r') as f:
            data = json.load(f)
            
        # Ensure required keys are present
        if 'pages_url' not in data or 'checks' not in data:
            print("Invalid data file: missing 'pages_url' or 'checks'", file=sys.stderr)
            sys.exit(1)
            
        # Run checks and get results
        results = run_selenium_checks(data['pages_url'], data['checks'])
        
        # Output results as JSON to stdout
        print(json.dumps(results))
        sys.exit(0)
        
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
