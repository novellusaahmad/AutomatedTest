import streamlit as st
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import subprocess
import json
import time
import os
import pandas as pd
import re
import csv
import io
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from PIL import Image
import schedule
import threading
import shutil
from pathlib import Path

# Constants
TARGET_WIDTH_PX = 100
TARGET_HEIGHT_PX = 100
SCREENSHOT_DIR = "screenshots"
RESULTS_DIR = "results"
TEST_CASES_FILE = "test_cases.json"
SCHEDULED_TESTS_FILE = "scheduled_tests.json"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

def get_image_scale(img_path, target_width_px=200, target_height_px=200):
    """Calculate scaling factors for image resizing"""
    try:
        with Image.open(img_path) as img:
            original_width, original_height = img.size
            x_scale = target_width_px / original_width
            y_scale = target_height_px / original_height
            return x_scale, y_scale
    except Exception as e:
        print(f"Error calculating image scale: {e}")
        return 1.0, 1.0

def identify_selectors_from_html(html_tag):
    """Extract CSS selectors from HTML snippet"""
    soup = BeautifulSoup(html_tag, 'html.parser')
    element = soup.find()
    
    if element is None:
        return None
    
    selectors = {}
    
    # ID selector
    if element.get('id'):
        selectors['id'] = element.get('id')
    
    # Name selector
    if element.get('name'):
        selectors['name'] = element.get('name')
    
    # CSS class selector
    if element.get('class'):
        selectors['css_selector'] = f".{' '.join(element.get('class'))}"
    
    # XPath selector
    xpath = f"//{element.name}"
    if element.get('id'):
        xpath += f"[@id='{element.get('id')}']"
    elif element.get('name'):
        xpath += f"[@name='{element.get('name')}']"
    if element.get('class'):
        xpath += f"[contains(@class, '{' '.join(element.get('class'))}')]"
    selectors['xpath'] = xpath
    
    # Placeholder selector
    if element.get('placeholder'):
        selectors['placeholder'] = element.get('placeholder')
    
    return selectors

def load_test_cases():
    """Load saved test cases from JSON file"""
    if os.path.exists(TEST_CASES_FILE):
        with open(TEST_CASES_FILE, "r") as file:
            return json.load(file)
    return []

def save_test_cases(test_cases):
    """Save test cases to JSON file"""
    with open(TEST_CASES_FILE, "w") as file:
        json.dump(test_cases, file, indent=4)

def load_scheduled_tests():
    """Load scheduled tests from JSON file"""
    if os.path.exists(SCHEDULED_TESTS_FILE):
        with open(SCHEDULED_TESTS_FILE, "r") as file:
            return json.load(file)
    return []

def save_scheduled_tests(scheduled_tests):
    """Save scheduled tests to JSON file"""
    with open(SCHEDULED_TESTS_FILE, "w") as file:
        json.dump(scheduled_tests, file, indent=4)

def start_recording(url):
    """Launch browser and record user interactions across pages."""
    options = Options()
    options.add_argument("--incognito")
    driver = webdriver.Chrome(service=ChromeService(), options=options)
    driver.maximize_window()

    recorder_script = """
        window.__recordedSteps = JSON.parse(localStorage.getItem('__recordedSteps') || '[]');
        function recordStep(step){
            window.__recordedSteps.push(step);
            localStorage.setItem('__recordedSteps', JSON.stringify(window.__recordedSteps));
        }
        function cssPath(el){
            if (!(el instanceof Element)) return '';
            var path = [];
            while (el.nodeType === Node.ELEMENT_NODE){
                var selector = el.nodeName.toLowerCase();
                if (el.id){
                    selector += '#' + el.id;
                    path.unshift(selector);
                    break;
                } else {
                    var sib = el, nth = 1;
                    while(sib = sib.previousElementSibling){
                        if (sib.nodeName.toLowerCase() == selector) nth++;
                    }
                    if (nth != 1) selector += ':nth-of-type(' + nth + ')';
                }
                path.unshift(selector);
                el = el.parentNode;
            }
            return path.join(' > ');
        }
        document.addEventListener('click', function(e){
            recordStep({
                action: 'click',
                selector_type: 'css_selector',
                selector_value: cssPath(e.target)
            });
        }, true);
        document.addEventListener('input', function(e){
            recordStep({
                action: 'input',
                selector_type: 'css_selector',
                selector_value: cssPath(e.target),
                text: e.target.value
            });
        }, true);
        window.addEventListener('scroll', function(){
            recordStep({
                action: 'scroll',
                x: window.scrollX,
                y: window.scrollY
            });
        }, true);
    """

    # Ensure the recorder script is injected on every new document
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': recorder_script})
    driver.get(url)
    # Start with a clean slate for this session
    driver.execute_script("localStorage.removeItem('__recordedSteps'); window.__recordedSteps = [];")
    return driver

def stop_recording(driver, start_url):
    """Stop recording and return recorded steps."""
    events_json = driver.execute_script("return localStorage.getItem('__recordedSteps');")
    driver.quit()
    events = json.loads(events_json) if events_json else []
    steps = [{"action": "visit", "url": start_url, "wait": 1}]
    for event in events:
        action = event.get("action")
        if action == "scroll":
            step = {
                "action": "scroll",
                "x": event.get("x", 0),
                "y": event.get("y", 0),
                "wait": 1
            }
        else:
            step = {
                "action": action,
                "selector_type": event.get("selector_type"),
                "selector_value": event.get("selector_value"),
                "wait": 1
            }
            if action == "input":
                step["text"] = event.get("text", "")
        steps.append(step)
    return steps

def find_element(driver, selector_type, selector_value, index=0):
    """Universal element finder with multiple selector types"""
    selectors = {
        "id": By.ID,
        "name": By.NAME,
        "xpath": By.XPATH,
        "css_selector": By.CSS_SELECTOR,
        "class_name": By.CLASS_NAME,
        "tag_name": By.TAG_NAME,
        "link_text": By.LINK_TEXT,
        "partial_link_text": By.PARTIAL_LINK_TEXT,
        "placeholder": By.XPATH
    }
    if selector_type == "placeholder":
        selector_value = f"//*[@placeholder='{selector_value}']"
    elements = driver.find_elements(selectors[selector_type], selector_value)
    if elements and index < len(elements):
        return elements[index]
    else:
        raise Exception(f"No element found at index {index} for {selector_type}: {selector_value}")

def substitute_placeholders(text, csv_row):
    """Replace {{placeholders}} with values from CSV row"""
    if not isinstance(text, str) or csv_row is None:
        return text
    placeholders = re.findall(r"\{\{(.*?)\}\}", text)
    for placeholder in placeholders:
        value = csv_row.get(placeholder) if isinstance(csv_row, (dict, pd.Series)) else None
        text = text.replace(f"{{{{{placeholder}}}}}", str(value) if value and pd.notna(value) else '')
    return text

def capture_notification(driver):
    """Capture and close any notifications/alerts"""
    try:
        WebDriverWait(driver, 3).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//*[contains(@class, 'Vue-Toastification__toast-body') or @role='alert' or contains(@class, 'el-form-item__error')]")
            )
        )
        elements = driver.find_elements(By.XPATH, "//*[contains(@class, 'Vue-Toastification__toast-body') or @role='alert' or contains(@class, 'el-form-item__error')]")
        notifications = [el.text.strip() for el in elements if el.text.strip()]
        time.sleep(2)
        for el in elements:
            try:
                close_buttons = driver.find_elements(By.CSS_SELECTOR, ".Vue-Toastification__close-button")
                time.sleep(2)
                for button in close_buttons:
                    try:
                        button.click()
                    except Exception as e:
                        print(f"Error clicking toast close button: {e}")
                        time.sleep(2)
            except:
                pass
        return notifications
    except:
        return []

def save_test_result(result_data, test_name):
    """Save test results to JSON file with timestamp"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{test_name}_{timestamp}.json"
    os.makedirs(RESULTS_DIR, exist_ok=True)
    filepath = os.path.join(RESULTS_DIR, filename)
    
    full_data = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "logs": result_data
    }
    
    with open(filepath, "w") as f:
        json.dump(full_data, f, indent=2)
    
    return filepath

def get_historical_results():
    """Load all historical test results"""
    results = []
    if os.path.exists(RESULTS_DIR):
        for filename in sorted(os.listdir(RESULTS_DIR), reverse=True):
            if filename.endswith(".json"):
                filepath = os.path.join(RESULTS_DIR, filename)
                try:
                    with open(filepath, "r") as f:
                        result_data = json.load(f)
                        # Extract test name and timestamp from filename
                        parts = filename.rsplit("_", 1)
                        test_name = parts[0]
                        timestamp_str = parts[1].replace(".json", "")
                        
                        try:
                            timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                        except ValueError:
                            timestamp = datetime.fromtimestamp(os.path.getmtime(filepath))
                        
                        results.append({
                            "filename": filename,
                            "filepath": filepath,
                            "test_name": test_name,
                            "timestamp": timestamp,
                            "data": result_data
                        })
                except Exception as e:
                    print(f"Error loading result file {filename}: {e}")
    return results

def run_scheduled_test(test_name, headless=True, csv_path=None):
    """Execute a scheduled test in background with optional CSV data"""
    test_cases = load_test_cases()
    test_case = next((tc for tc in test_cases if tc["name"] == test_name), None)
    
    if not test_case:
        print(f"Test case {test_name} not found")
        return
    
    logs_output = []
    
    try:
        if csv_path and os.path.exists(csv_path):
            csv_data = pd.read_csv(csv_path)
            for idx, row in csv_data.iterrows():
                user_id = row.get("LoginEmail", f"Row {idx+1}")
                print(f"Running scheduled test '{test_name}' for '{user_id}'")
                logs = list(run_test_case(test_case, headless=headless, repeat=1, csv_row=row))
                logs_output.extend(logs)
        else:
            print(f"Running scheduled test '{test_name}'")
            logs = list(run_test_case(test_case, headless=headless, repeat=1))
            logs_output.extend(logs)
        
        # Save the result
        result_data = {
            "test_name": test_name,
            "timestamp": datetime.now().isoformat(),
            "logs": logs_output,
            "csv_used": csv_path if csv_path else None
        }
        
        save_test_result(result_data, test_name)
        print(f"Completed scheduled test for {test_name}")
    except Exception as e:
        print(f"Error running scheduled test: {e}")

def run_test_case(test_case, headless=True, repeat=1, csv_row=None):
    """Execute a test case and yield step results"""
    logs_output = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for _ in range(repeat):
        try:
            options = Options()
            if headless:
                options.add_argument("--headless=new")
            options.add_argument("--incognito")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-cache")
            
            driver = webdriver.Chrome(service=ChromeService(), options=options)
            driver.maximize_window()
            driver.delete_all_cookies()
            driver.refresh()
            driver.refresh()  

            for step in test_case["steps"]:
                action = step["action"]
                wait_time = step.get("wait", 0)
                index = step.get("index", 0)
                
                step_log = {
                    "action": action,
                    "selector_type": step.get("selector_type", ""),
                    "selector_value": step.get("selector_value", ""),
                    "url": step.get("url", ""),
                    "text": step.get("text", ""),
                    "x": step.get("x", 0),
                    "y": step.get("y", 0),
                    "index": index,
                    "wait_time": wait_time,
                    "actual_url": "",
                    "status": "",
                    "notifications": []
                }

                if action == "visit":
                    driver.refresh()
                    expected_url = substitute_placeholders(step["url"], csv_row)
                    driver.get(expected_url)
                    time.sleep(1)
                    actual_url = driver.current_url
                    step_log["actual_url"] = actual_url
                    step_log["status"] = "✅ Success" if expected_url.rstrip('/') == actual_url.rstrip('/') else "❌ No Access"
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    notifications = capture_notification(driver)
                    if notifications:
                        step_log["notifications"] = notifications
                        if any("success" in str(n).lower() for n in notifications):
                            step_log["status"] = "✅ Success"
                        else:
                            step_log["status"] = "❌ Failed"

                elif action == "click":
                    find_element(driver, step["selector_type"], step["selector_value"], index).click()
                    step_log["status"] = "✅ Clicked"
                    time.sleep(1)                    
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    notifications = capture_notification(driver)
                    if notifications:
                        step_log["notifications"] = notifications
                        if any("success" in str(n).lower() for n in notifications):
                            step_log["status"] = "✅ Success"
                        else:
                            step_log["status"] = "❌ Failed"

                elif action == "input":
                    element = find_element(driver, step["selector_type"], step["selector_value"], index)
                    element.clear()
                    value = substitute_placeholders(step["text"], csv_row)
                    element.send_keys(value)
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    step_log["status"] = f"✅ Input '{value}'"

                elif action == "assert":
                    value = substitute_placeholders(step["text"], csv_row)
                    assert value in driver.page_source
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    step_log["status"] = f"✅ Asserted '{value}'"

                elif action == "select_dropdown":
                    dropdown = find_element(driver, step["selector_type"], step["selector_value"], index)
                    dropdown.click()
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    time.sleep(1)

                    expected_text = substitute_placeholders(step["text"], csv_row).strip()
                    items = driver.find_elements(By.CSS_SELECTOR, "li.el-dropdown-menu__item")
                    selected = False
                    for item in items:
                        if item.is_displayed() and item.text.strip() == expected_text:
                            item.click()
                            step_log["status"] = f"✅ Selected '{item.text.strip()}'"
                            selected = True
                            break
                    if not selected:
                        step_log["status"] = f"❌ Dropdown item '{expected_text}' not found"
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    notifications = capture_notification(driver)
                    if notifications:
                        step_log["notifications"] = notifications
                        if any("success" in str(n).lower() for n in notifications):
                            step_log["status"] = "✅ Success"
                        else:
                            step_log["status"] = "❌ Failed"

                elif action == "scroll":
                    x = step.get("x", 0)
                    y = step.get("y", 0)
                    driver.execute_script("window.scrollTo(arguments[0], arguments[1]);", x, y)
                    screenshot_filename = f"{SCREENSHOT_DIR}/step_{timestamp}_{action}_{int(time.time()*1000)}.png"
                    driver.save_screenshot(screenshot_filename)
                    step_log["screenshot"] = screenshot_filename
                    step_log["status"] = f"✅ Scrolled to ({x}, {y})"

                if csv_row is not None and "LoginEmail" in csv_row:
                    step_log["LoginEmail"] = csv_row["LoginEmail"]
                logs_output.append(step_log)
                yield step_log
                if wait_time > 0:
                    time.sleep(wait_time)

            driver.quit()
        except Exception as e:
            logs_output.append({"status": f"❌ Error: {e}"})
            try:
                driver.quit()
            except:
                pass
    return logs_output

def create_excel_with_screenshots(logs_df, writer):
    """Create Excel file with embedded screenshots"""
    workbook = writer.book
    cell_format = workbook.add_format({'valign': 'top'})
    header_format = workbook.add_format({
        'bold': True,
        'bg_color': '#1F4E78',
        'font_color': 'white',
        'valign': 'top'
    })

    if "LoginEmail" in logs_df.columns:
        for email in logs_df["LoginEmail"].dropna().unique():
            sheet_df = logs_df[logs_df["LoginEmail"] == email]
            sheet_name = re.sub(r'[^A-Za-z0-9]', '_', str(email))[:31]
            sheet_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1, header=False)
            worksheet = writer.sheets[sheet_name]

            # Write headers
            for col_num, value in enumerate(sheet_df.columns.values):
                worksheet.write(0, col_num, value, header_format)
            
            # Write data and handle screenshots
            for row_num in range(1, len(sheet_df) + 1):
                for col_num, col_name in enumerate(sheet_df.columns):
                    cell_value = sheet_df.iloc[row_num - 1, col_num]
                    
                    if col_name == "screenshot":
                        # Set column width for screenshot column
                        worksheet.set_column(col_num, col_num, 30)
                        
                        # Insert image if path exists
                        if isinstance(cell_value, str) and os.path.exists(cell_value):
                            try:
                                x_scale, y_scale = get_image_scale(cell_value, 200, 150)
                                worksheet.set_row(row_num, 120)
                                worksheet.insert_image(
                                    row_num, col_num, cell_value,
                                    {'x_scale': x_scale, 'y_scale': y_scale, 'object_position': 1}
                                )
                            except Exception as e:
                                worksheet.write(row_num, col_num, f"Image Error: {str(e)}", cell_format)
                    else:
                        worksheet.write(row_num, col_num, str(cell_value), cell_format)
                        # Auto-adjust column width
                        max_len = max(len(str(cell_value)), len(col_name)) + 2
                        worksheet.set_column(col_num, col_num, max_len)
    else:
        # Fallback for tests without LoginEmail
        sheet_name = 'Test Results'
        logs_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1, header=False)
        worksheet = writer.sheets[sheet_name]
        
        # Write headers
        for col_num, value in enumerate(logs_df.columns.values):
            worksheet.write(0, col_num, value, header_format)
        
        # Write data
        for row_num in range(1, len(logs_df) + 1):
            for col_num, col_name in enumerate(logs_df.columns):
                cell_value = logs_df.iloc[row_num - 1, col_num]
                
                if col_name == "screenshot":
                    worksheet.set_column(col_num, col_num, 30)
                    if isinstance(cell_value, str) and os.path.exists(cell_value):
                        try:
                            x_scale, y_scale = get_image_scale(cell_value, 200, 150)
                            worksheet.set_row(row_num, 120)
                            worksheet.insert_image(
                                row_num, col_num, cell_value,
                                {'x_scale': x_scale, 'y_scale': y_scale, 'object_position': 1}
                            )
                        except Exception as e:
                            worksheet.write(row_num, col_num, f"Image Error: {str(e)}", cell_format)
                else:
                    worksheet.write(row_num, col_num, str(cell_value), cell_format)
                    max_len = max(len(str(cell_value)), len(col_name)) + 2
                    worksheet.set_column(col_num, col_num, max_len)

# Streamlit App Configuration
st.set_page_config(
    page_title="Automation Test Dashboard",
    page_icon="Logo.png",
    layout="wide",
)

st.markdown(
    """
    <style>
        .block-container {padding-top: 2rem;}
        .stButton>button {
            background-color: #4F46E5;
            color: white;
            border-radius: 4px;
            border: none;
        }
        .stButton>button:hover {
            background-color: #4338CA;
            color: white;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

logo_col, title_col = st.columns([1, 5])
with logo_col:
    st.image("Logo.png", width=64)
with title_col:
    st.title("Test Automation Framework")
    st.caption("Create, schedule, and run automated tests with ease.")

# Initialize session state
if "steps" not in st.session_state:
    st.session_state.steps = []
if "editing_index" not in st.session_state:
    st.session_state.editing_index = None
if "active_test_name" not in st.session_state:
    st.session_state.active_test_name = ""
if "scheduled_tests" not in st.session_state:
    st.session_state.scheduled_tests = load_scheduled_tests()
if "record_driver" not in st.session_state:
    st.session_state.record_driver = None
# Store the URL used for recording separately from the text input to avoid
# conflicts with widget-managed keys.
if "recording_url" not in st.session_state:
    st.session_state.recording_url = ""
if "record_url_input" not in st.session_state:
    st.session_state.record_url_input = ""

# Sidebar for test case management
with st.sidebar:
    st.image("Logo.png", width=200)
    st.header("📦 Test Case Management")
    
    mode = st.radio("Mode", ["Create New", "Edit Existing", "Delete"])

    if mode == "Create New":
        test_name = st.text_input("Test Name", key="create_name")
        if test_name in [tc["name"] for tc in load_test_cases()]:
            st.warning("Test name must be unique.")
            test_name = None
    elif mode == "Edit Existing":
        selected = st.selectbox("Select Test Case", [tc["name"] for tc in load_test_cases()])
        test_name = selected
        if st.session_state.active_test_name != selected:
            selected_case = next(tc for tc in load_test_cases() if tc["name"] == selected)
            st.session_state.steps = selected_case["steps"]
            st.session_state.active_test_name = selected
    elif mode == "Delete":
        del_name = st.selectbox("Select Test Case", [tc["name"] for tc in load_test_cases()])
        if st.button("⚠️ Confirm Delete"):
            updated_cases = [tc for tc in load_test_cases() if tc["name"] != del_name]
            save_test_cases(updated_cases)
            st.success(f"🗑️ Deleted '{del_name}'")
            st.rerun()
        test_name = None

    # Step editing interface
    editing = st.session_state.steps[st.session_state.editing_index] if st.session_state.editing_index is not None else None
    action = st.selectbox("Action", ["visit", "click", "input", "assert", "select_dropdown"],
                         index=(["visit", "click", "input", "assert", "select_dropdown"].index(editing["action"]) if editing else 0))
    wait_time = st.number_input("Wait Time", min_value=0, value=editing.get("wait", 0) if editing else 0)
    index = st.number_input("Element Index", min_value=0, value=editing.get("index", 0) if editing else 0) if action != "visit" else 0

    if action == "visit":
        url = st.text_input("URL", value=editing.get("url", "") if editing else "")
    else:
        selector_type = st.selectbox("Selector Type", [
            "id", "name", "xpath", "css_selector", "class_name", "tag_name", "link_text", "partial_link_text", "placeholder"
        ], index=(["id", "name", "xpath", "css_selector", "class_name", "tag_name", "link_text", "partial_link_text", "placeholder"].index(editing.get("selector_type", "xpath")) if editing else 0))
        selector_value = st.text_input("Selector Value", value=editing.get("selector_value", "") if editing else "")
        text = st.text_input("Text", value=editing.get("text", "") if editing and action in ["input", "assert", "select_dropdown"] else "") if action in ["input", "assert", "select_dropdown"] else None

    # Step editing buttons
    if st.session_state.editing_index is not None:
        if st.button("💾 Save Edited Step"):
            idx = st.session_state.editing_index
            if action == "visit":
                st.session_state.steps[idx] = {"action": "visit", "url": url, "wait": wait_time}
            else:
                step = {"action": action, "selector_type": selector_type, "selector_value": selector_value, "wait": wait_time, "index": index}
                if action in ["input", "assert", "select_dropdown"]:
                    step["text"] = text
                st.session_state.steps[idx] = step
            st.session_state.editing_index = None
            st.rerun()
        if st.button("❌ Cancel"):
            st.session_state.editing_index = None
            st.rerun()
    else:
        if st.button("Add Step"):
            if action == "visit" and url:
                st.session_state.steps.append({"action": "visit", "url": url, "wait": wait_time})
            elif action != "visit":
                step = {"action": action, "selector_type": selector_type, "selector_value": selector_value, "wait": wait_time, "index": index}
                if action in ["input", "assert", "select_dropdown"]:
                    step["text"] = text
                st.session_state.steps.append(step)
            st.rerun()

    # Recording helper
    st.subheader("🎥 Record Steps")
    record_url = st.text_input("URL to Record", key="record_url_input")
    col_rec1, col_rec2 = st.columns(2)
    with col_rec1:
        if st.session_state.record_driver is None and st.button("Start Recording"):
            if record_url:
                st.session_state.record_driver = start_recording(record_url)
                # Save the URL used for recording so it can be referenced when
                # stopping the recording later.
                st.session_state.recording_url = record_url
                st.success("Recording started. Interact with the browser and then stop recording.")
            else:
                st.warning("Please provide a URL to record.")
    with col_rec2:
        if st.session_state.record_driver is not None and st.button("Stop Recording"):
            recorded_steps = stop_recording(st.session_state.record_driver, st.session_state.recording_url)
            st.session_state.steps.extend(recorded_steps)
            st.session_state.record_driver = None
            st.success("Recording stopped and steps added.")

    # HTML selector helper
    st.subheader("🔍 Identify Selector from HTML Tag")
    html_tag_input = st.text_area("Enter the HTML Tag", height=200)
    if html_tag_input:
        selectors = identify_selectors_from_html(html_tag_input)
        if selectors:
            st.write("### Suggested Selectors:")
            for selector_type, selector_value in selectors.items():
                st.write(f"- **{selector_type}**: `{selector_value}`")
        else:
            st.warning("Unable to parse the HTML tag. Please check the input format.")

# Main content area
# Test Case Steps Display
st.subheader("Test Case Steps")
for i, step in enumerate(st.session_state.steps):
    col1, col2, col3, col4, col5 = st.columns([5, 1, 1, 1, 1])
    with col1:
        st.write(step)
    with col2:
        if st.button("✏️", key=f"edit_{i}"):
            st.session_state.editing_index = i
            st.rerun()
    with col3:
        if st.button("🗑️", key=f"del_{i}"):
            st.session_state.steps.pop(i)
            st.rerun()
    with col4:
        if i > 0 and st.button("↑", key=f"move_up_{i}"):
            st.session_state.steps[i], st.session_state.steps[i - 1] = st.session_state.steps[i - 1], st.session_state.steps[i]
            st.rerun()
    with col5:
        if i < len(st.session_state.steps) - 1 and st.button("↓", key=f"move_down_{i}"):
            st.session_state.steps[i], st.session_state.steps[i + 1] = st.session_state.steps[i + 1], st.session_state.steps[i]
            st.rerun()

# Save Test Case Button
if st.button("💾 Save Test Case") and test_name:
    existing = next((tc for tc in load_test_cases() if tc["name"] == test_name), None)
    if existing:
        existing["steps"] = st.session_state.steps
    else:
        updated_cases = load_test_cases()
        updated_cases.append({"name": test_name, "steps": st.session_state.steps})
        save_test_cases(updated_cases)
    st.success(f"✅ Test case '{test_name}' saved!")
    st.session_state.steps = []
    st.session_state.active_test_name = ""
    st.rerun()

# Test Scheduling Section
with st.expander("⏰ Schedule Tests", expanded=False):
    st.subheader("Schedule Test Execution")
    
    selected_schedule_test = st.selectbox("Select Test to Schedule", [tc["name"] for tc in load_test_cases()])
    schedule_time = st.time_input("Schedule Time")
    schedule_days = st.multiselect("Repeat on Days", 
                                 ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                                 default=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    
    # Add CSV upload for scheduled tests
    scheduled_csv = st.file_uploader("Upload CSV for Scheduled Test (Optional)", type=["csv"])
    csv_path = None
    if scheduled_csv:
        csv_path = os.path.join(RESULTS_DIR, f"scheduled_{selected_schedule_test}_data.csv")
        with open(csv_path, "wb") as f:
            f.write(scheduled_csv.getvalue())
    
    if st.button("📅 Schedule Test"):
        scheduled_test = {
            "test_name": selected_schedule_test,
            "time": str(schedule_time),
            "days": schedule_days,
            "created_at": datetime.now().isoformat(),
            "csv_path": csv_path if scheduled_csv else None
        }
        
        updated_scheduled = load_scheduled_tests()
        updated_scheduled.append(scheduled_test)
        save_scheduled_tests(updated_scheduled)
        st.session_state.scheduled_tests = updated_scheduled
        
        # Update the scheduler
        if 'scheduler_thread' in st.session_state:
            schedule.clear()
            for test in updated_scheduled:
                days = test['days']
                time_str = test['time']
                test_name = test['test_name']
                csv_path = test.get('csv_path')
                
                time_obj = datetime.strptime(time_str, "%H:%M:%S").time()
                for day in days:
                    getattr(schedule.every(), day.lower()).at(time_obj.strftime("%H:%M")).do(
                        run_scheduled_test, 
                        test_name=test_name, 
                        headless=True,
                        csv_path=csv_path
                    )
        
        st.success(f"✅ Test '{selected_schedule_test}' scheduled for {schedule_time} on {', '.join(schedule_days)}")

    st.subheader("Scheduled Tests")
    if st.session_state.scheduled_tests:
        for i, scheduled_test in enumerate(st.session_state.scheduled_tests):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"**{scheduled_test['test_name']}** at {scheduled_test['time']} on {', '.join(scheduled_test['days'])}")
                if scheduled_test.get('csv_path'):
                    st.caption(f"Using CSV: {os.path.basename(scheduled_test['csv_path'])}")
            with col2:
                if st.button("❌", key=f"delete_scheduled_{i}"):
                    updated_scheduled = load_scheduled_tests()
                    updated_scheduled.pop(i)
                    save_scheduled_tests(updated_scheduled)
                    st.session_state.scheduled_tests = updated_scheduled
                    st.rerun()
    else:
        st.info("No tests scheduled yet")

# Historical Results Section
with st.expander("📜 Historical Test Results", expanded=True):
    st.subheader("Previous Test Runs")
    
    historical_results = get_historical_results()
    
    if historical_results:
        # Filter options
        col1, col2 = st.columns(2)
        with col1:
            filter_test = st.selectbox("Filter by Test", ["All"] + list(set(r["test_name"] for r in historical_results)))
        with col2:
            days_back = st.slider("Show results from last N days", 1, 30, 7)
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        filtered_results = [r for r in historical_results 
                          if r["timestamp"] >= cutoff_date and 
                          (filter_test == "All" or r["test_name"] == filter_test)]
        
        if not filtered_results:
            st.info("No results match your filters")
        else:
            for result in filtered_results:
                with st.expander(f"{result['test_name']} - {result['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}", expanded=False):
                    # Display basic info
                    col1, col2 = st.columns([3,1])
                    with col1:
                        st.write(f"**Test Name:** {result['test_name']}")
                        st.write(f"**Run Time:** {result['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
                        if result['data'].get('csv_used'):
                            st.write(f"**CSV Used:** {os.path.basename(result['data']['csv_used'])}")
                    
                    # Create a DataFrame from the logs
                    try:
                        logs_df = pd.DataFrame(result['data']['logs'])
                        
                        if not logs_df.empty:
                            # Display the logs
                            st.dataframe(logs_df)
                            
                            # Download buttons
                            st.write("### Download Options")
                            col1, col2, col3 = st.columns(3)
                            
                            with col1:
                                # CSV Download
                                csv = logs_df.to_csv(index=False).encode('utf-8')
                                st.download_button(
                                    label="📥 Download CSV",
                                    data=csv,
                                    file_name=f"{result['test_name']}_{result['timestamp'].strftime('%Y%m%d_%H%M%S')}.csv",
                                    mime='text/csv',
                                    key=f"csv_{result['filename']}"
                                )
                            
                            with col2:
                                # Excel Download with screenshots
                                excel_buffer = io.BytesIO()
                                with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                                    create_excel_with_screenshots(logs_df, writer)
                                excel_buffer.seek(0)
                                st.download_button(
                                    label="📥 Download Excel with Screenshots",
                                    data=excel_buffer,
                                    file_name=f"{result['test_name']}_{result['timestamp'].strftime('%Y%m%d_%H%M%S')}.xlsx",
                                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                    key=f"excel_{result['filename']}"
                                )
                            
                            with col3:
                                # Full JSON Download
                                json_data = json.dumps(result['data'], indent=2).encode('utf-8')
                                st.download_button(
                                    label="📥 Download JSON",
                                    data=json_data,
                                    file_name=result['filename'],
                                    mime='application/json',
                                    key=f"json_{result['filename']}"
                                )
                    except Exception as e:
                        st.error(f"Error displaying results: {e}")
    else:
        st.info("No historical test results available")

# Test Execution Section
st.subheader("🚀 Run Tests")
selected_cases = st.multiselect("Select Test Cases", [tc["name"] for tc in load_test_cases()])
repeat = st.number_input("Repeat Count", min_value=1, value=1)
headless = st.checkbox("Run Headless", value=True)

# CSV Data Upload
st.subheader("📄 Load CSV Data")
uploaded_file = st.file_uploader("Upload CSV File", type=["csv"])
csv_data = pd.read_csv(uploaded_file) if uploaded_file else None
if csv_data is not None:
    st.write("✅ CSV Loaded:")
    st.dataframe(csv_data)

# Run Tests Button
logs_output = []
if st.button("▶️ Run Selected Tests"):
    st.subheader("📜 Live Logs")

    total_runs = len(selected_cases) * repeat * (len(csv_data) if csv_data is not None else 1)
    progress_bar = st.progress(0)
    status_box = st.empty()
    log_container = st.container()
    completed = 0

    for name in selected_cases:
        test = next(tc for tc in load_test_cases() if tc["name"] == name)
        if csv_data is not None:
            for idx, row in csv_data.iterrows():
                user_id = row.get("LoginEmail", f"Row {idx+1}")
                status_box.info(f"Running `{name}` for `{user_id}` ({completed+1}/{total_runs})")
                logs = list(run_test_case(test, headless=headless, repeat=repeat, csv_row=row))
                group_title = f"🧪 {name} | 👤 {user_id}"
                with log_container.expander(group_title, expanded=False):
                    for i, log in enumerate(logs):
                        st.markdown(f"### 🔹 Step {i+1}: `{log.get('action', '').upper()}` - {log.get('status', 'Unknown')}")
                        if log.get("notifications"):
                            st.markdown("**Notifications:**")
                            st.write(log["notifications"])
                        if log.get("screenshot") and os.path.exists(log["screenshot"]):
                            st.image(log["screenshot"], caption="📸 Screenshot", use_container_width=True)
                        st.markdown("---")

                    logs_output.extend(logs)
                completed += 1
                progress_bar.progress(completed / total_runs)

        else:
            for r in range(repeat):
                status_box.info(f"Running `{name}` ({completed+1}/{total_runs})")
                logs = list(run_test_case(test, headless=headless, repeat=1))

                for i, log in enumerate(logs):
                    with log_container.expander(f"🔹 Step {i+1}: {log.get('action', '').upper()} - {log.get('status', 'Unknown')}"):
                        st.markdown(f"**Selector Type:** `{log.get('selector_type', '')}`")
                        st.markdown(f"**Selector Value:** `{log.get('selector_value', '')}`")
                        st.markdown(f"**Text:** `{log.get('text', '')}`")
                        st.markdown(f"**Wait Time:** `{log.get('wait_time', '')}`")
                        st.markdown(f"**Actual URL:** `{log.get('actual_url', '')}`")
                        if log.get("notifications"):
                            st.markdown("**Notifications:**")
                            st.write(log["notifications"])
                        if log.get("screenshot") and os.path.exists(log["screenshot"]):
                            st.image(log["screenshot"], caption="📸 Screenshot", use_container_width=True)

                    logs_output.append(log)
                    time.sleep(0.05)

                completed += 1
                progress_bar.progress(completed / total_runs)

    progress_bar.empty()
    status_box.success("🎉 All tests completed!")

    # Save the test results
    for name in selected_cases:
        result_data = {
            "test_name": name,
            "timestamp": datetime.now().isoformat(),
            "logs": logs_output,
            "csv_used": uploaded_file.name if uploaded_file else None
        }
        save_test_result(result_data, name)

    # Display results summary
    logs_df = pd.DataFrame(logs_output)

    if "LoginEmail" in logs_df.columns:
        cols = ["LoginEmail"] + [col for col in logs_df.columns if col != "LoginEmail"]
        logs_df = logs_df[cols]

    st.write("### Test Results Summary")
    st.dataframe(logs_df)

    # Download options
    if not logs_df.empty:
        file_base_name = "_".join(selected_cases).replace(" ", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # CSV Download
        csv_bytes = logs_df.to_csv(index=False).encode("utf-8-sig")
        csv_filename = f"{file_base_name}_{timestamp}_logs.csv"
        st.download_button("Download Log CSV", data=csv_bytes, file_name=csv_filename, mime="text/csv")

        # Excel Download
        excel_filename = f"{file_base_name}_{timestamp}_logs.xlsx"
        excel_data = io.BytesIO()

        with pd.ExcelWriter(excel_data, engine='xlsxwriter') as writer:
            create_excel_with_screenshots(logs_df, writer)
        
        excel_data.seek(0)
        st.download_button("Download Log Excel", data=excel_data, file_name=excel_filename,
                         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        # Cleanup screenshots
        for path in logs_df["screenshot"].dropna():
            if isinstance(path, str) and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    st.warning(f"⚠️ Could not delete {path}: {e}")

# Background scheduler thread
def run_scheduler():
    """Background thread to run scheduled tests"""
    while True:
        schedule.run_pending()
        time.sleep(60)

# Start scheduler thread if not already running
if 'scheduler_thread' not in st.session_state:
    # Load scheduled tests and set up schedule jobs
    scheduled_tests = load_scheduled_tests()
    for test in scheduled_tests:
        days = test['days']
        time_str = test['time']
        test_name = test['test_name']
        csv_path = test.get('csv_path')
        
        time_obj = datetime.strptime(time_str, "%H:%M:%S").time()
        for day in days:
            getattr(schedule.every(), day.lower()).at(time_obj.strftime("%H:%M")).do(
                run_scheduled_test, 
                test_name=test_name, 
                headless=True,
                csv_path=csv_path
            )
    
    # Start the scheduler thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    st.session_state.scheduler_thread = scheduler_thread
