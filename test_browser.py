from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    page.goto("https://automationexercise.com/contact_us")

    # Find all input and textarea elements on the page
    form_elements = page.query_selector_all("input, textarea")

    print("\n--- FOUND FORM FIELDS ---")
    for element in form_elements:
        # Extract technical attributes from the HTML element
        field_type = element.get_attribute("type") or "textarea"
        field_name = element.get_attribute("name") or "N/A"
        field_placeholder = element.get_attribute("placeholder") or "N/A"

        # We only care about fields a user actually types into
        if field_type not in ["submit", "hidden", "file"]:
            print(f"Type: {field_type} | Name: {field_name} | Placeholder: {field_placeholder}")

    print("-------------------------\n")

    browser.close()