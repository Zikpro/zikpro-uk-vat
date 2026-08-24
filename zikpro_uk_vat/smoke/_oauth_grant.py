"""Headless OAuth grant for the smoke harness — drives HMRC's sandbox
authorize -> sign-in -> grant with a test user, landing on our /oauth-callback
so tokens get saved. Invoked as a subprocess by hmrc_smoke (Playwright can't run
inside the bench worker synchronously). Prints GRANT_OK / GRANT_FAIL.

argv: <authorize_url> <userId> <password>
"""

import sys

from playwright.sync_api import sync_playwright


def _click_if(page, selector):
	loc = page.locator(selector).first
	if loc.count() and loc.is_visible():
		loc.click()
		return True
	return False


def main():
	url, user_id, password = sys.argv[1], sys.argv[2], sys.argv[3]
	seen = []
	with sync_playwright() as pw:
		browser = pw.chromium.launch(headless=True)
		page = browser.new_context(viewport={"width": 1200, "height": 900}).new_page()
		# Record every top-level document request. Our /oauth-callback bounces via a
		# server-side 301 to ?connect=success|error|denied and the desk login redirect
		# then drops that query string — so neither the final URL nor framenavigated
		# can prove the grant worked, but the redirect target does appear as a request.
		page.on(
			"request",
			lambda req: seen.append(req.url) if req.resource_type == "document" else None,
		)
		page.goto(url, wait_until="domcontentloaded", timeout=60000)
		page.wait_for_timeout(2000)
		_click_if(page, "button:has-text('Reject additional cookies')")
		page.wait_for_timeout(500)
		for _ in range(9):
			if "oauth-callback" in page.url or "vat-cockpit" in page.url or "/login" in page.url:
				break
			if page.locator("#userId").count() and page.locator("#userId").is_visible():
				page.fill("#userId", user_id)
				page.fill("#password", password)
				page.click("button:has-text('Sign in'), button[type=submit], .govuk-button")
				page.wait_for_timeout(3500)
				continue
			if (
				_click_if(page, "button:has-text('Continue')")
				or _click_if(page, "button:has-text('Grant')")
				or _click_if(page, "a:has-text('Continue')")
				or _click_if(page, ".govuk-button")
			):
				page.wait_for_timeout(3000)
				continue
			break
		page.wait_for_timeout(1500)
		browser.close()

	# Only 'connect=success' proves our callback exchanged the code and saved tokens.
	# Landing on /app/vat-cockpit alone is NOT proof — the callback redirects there on
	# failure too (?connect=error), which previously produced a false GRANT_OK.
	if any("connect=success" in u for u in seen):
		print("GRANT_OK")
	else:
		outcome = next((u for u in seen if "connect=" in u), None)
		trail = " -> ".join(u.split("?")[0][:60] for u in seen[-4:])
		print(f"GRANT_FAIL outcome={outcome} trail={trail}")


if __name__ == "__main__":
	main()
