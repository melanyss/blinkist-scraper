import os
import time
import requests
import json

import sys
from shutil import copyfile as copy_file

import chromedriver_autoinstaller
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.common.exceptions import ElementNotInteractableException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# from utils import *
from utils import get_book_pretty_filepath
from utils import get_book_dump_filename
from utils import sanitize_name

import logger

log = logger.get(f"blinkistscraper.{__name__}")


def has_login_cookies():
    return os.path.exists("cookies.json")


def get_login_cookies():
    with open("cookies.json", "r") as f:
        return json.load(f)


def load_login_cookies(driver):
    for cookie in get_login_cookies():
        driver.add_cookie(cookie)


def store_login_cookies(driver):
    cookies = driver.get_cookies()
    # filter out non-serializable expiry values
    for cookie in cookies:
        if "expiry" in cookie:
            cookie["expiry"] = int(cookie["expiry"])
    with open("cookies.json", "w") as f:
        json.dump(cookies, f, indent=2)


def initialize_driver(
    headless=True, with_ublock=False, no_sandbox=False, chromedriver_path=None,
    with_audio=False
):

    if not chromedriver_path:
        try:
            chromedriver_path = chromedriver_autoinstaller.install()
        except Exception as exception:
            log.critical(
                f"Failed to install the built-in chromedriver: {exception}\n"
                "download the correct version for your system at "
                "https://chromedriver.chromium.org/downloads and use the"
                "--chromedriver argument to point to the chromedriver "
                "executable."
            )
            sys.exit()

    log.info(f"Initialising chromedriver at {chromedriver_path}...")
    # chrome_options = Options()
    chrome_options = webdriver.ChromeOptions()
    if headless:
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("window-size=1920,1080")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--silent")
    chrome_options.add_argument("--disable-logging")
    if no_sandbox:
        chrome_options.add_argument("--no-sandbox")
    # removes the 'DevTools listening' log message
    chrome_options.add_experimental_option(
        "excludeSwitches", ["enable-logging"])
    # prevent Cloudflare from detecting ChromeDriver as bot
    chrome_options.add_experimental_option(
        "excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option(
        "useAutomationExtension", False)
    chrome_options.add_argument(
        "--disable-blink-features=AutomationControlled")

    if with_ublock:
        chrome_options.add_extension(
            os.path.join(os.getcwd(), "bin", "ublock", "ublock-extension.crx")
        )

    logs_path = os.path.join(os.getcwd(), "logs")
    if not (os.path.isdir(logs_path)):
        os.makedirs(logs_path)

    service = Service(
        executable_path=chromedriver_path,
        log_output=os.path.join(logs_path, "webdrive.log"),
    )

    if with_audio:
        # selenium-wire is only needed for audio interception
        from seleniumwire import webdriver as wire_webdriver
        driver = wire_webdriver.Chrome(
            service=service,
            options=chrome_options,
        )
    else:
        driver = webdriver.Chrome(
            service=service,
            options=chrome_options,
        )

    driver.execute_cdp_cmd(
        "Network.setUserAgentOverride",
        {
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/83.0.4103.97 Safari/537.36"
        },
    )
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
            Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
            })
        """
        },
    )

    if with_ublock:
        log.debug("Configuring uBlock")

        try:
            # set up uBlock
            driver.get(
                "chrome-extension://ilchdfhfciidacichehpmmjclkbfaecg"
                "/settings.html"
            )

            # check if extension loaded (Chrome may block outdated extensions)
            element = driver.find_elements(By.CLASS_NAME, "hidden")
            if not element:
                log.warning(
                    "uBlock extension page did not load (may be blocked by "
                    "Chrome). Continuing without uBlock."
                )
                driver.get("about:blank")
            else:
                # Un-hide the file upload button so we can use it
                driver.execute_script(
                    "document.getElementsByClassName('hidden')[0]"
                    ".className = ''",
                    element
                )
                # scroll down (for debugging)
                driver.execute_script("window.scrollTo(0, 2000)")
                uBlock_settings_file = str(
                    os.path.join(
                        os.getcwd(), "bin", "ublock", "ublock-settings.txt")
                )
                driver.find_element(
                    By.ID, "restoreFilePicker"
                ).send_keys(uBlock_settings_file)
                try:
                    WebDriverWait(driver, 3).until(EC.alert_is_present())
                    # click ok on pop up to accept overwrite
                    driver.switch_to.alert.accept()
                except TimeoutException:
                    log.error(
                        "Timeout waiting for ublock config overwrite alert")
                # leave uBlock config
                driver.get("about:blank")
        except Exception as e:
            log.warning(
                f"Failed to configure uBlock extension: {e}. "
                "Continuing without it."
            )
            driver.get("about:blank")

    return driver


def login(driver, language, email, password):
    # we need to navigate to a page first in order to load eventual cookies
    driver.get(f"https://www.blinkist.com/{language}/nc/login")
    is_logged_in = False

    # if we have any stored login cookie, load them into the driver
    if has_login_cookies():
        load_login_cookies(driver)
	
	# assume that a captcha needs to be solved, if no blinkist logo appears within 5sec
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.CLASS_NAME, "header__logo")
            )
        )
    except TimeoutException as ex:
        log.info("Please solve captcha to proceed!")
	
	# fail if captcha not solved within 60sec
    try:
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located(
                (By.CLASS_NAME, "header__logo")
            )
        )
    except TimeoutException as ex:
        log.error("Error. Captcha needs to be solved within 1 minute")
        return False

    # navigate to the login page
    sign_in_url = f"https://www.blinkist.com/{language}/nc/login"
    driver.get(sign_in_url)

    # click on cookie banner, if necessary
    try:
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located(
                (By.CLASS_NAME, "cookie-disclaimer__cta")
            )
        )
        driver.find_element(
            By.CLASS_NAME, "cookie-disclaimer__cta").click()
    except Exception:
        pass

    # check for the login email input. if not found, assume we're logged in
    try:
        driver.find_element(By.ID, "login-form_login_email")
    except NoSuchElementException:
        is_logged_in = True

    # if not logged in, autofill the email and password inputs with the
    # provided login credentials
    if not is_logged_in:
        log.info("Not logged into Blinkist. Logging in...")
        driver.find_element(
            By.ID, "login-form_login_email").send_keys(email)
        driver.find_element(
            By.ID, "login-form_login_password").send_keys(password)
        # click the "login" button
        driver.find_element(By.NAME, "commit").click()

    try:
        log.info("Logged into Blinkist. Loading Library...")
        # try to avert the captcha page by switching the URL
        library_url = f"https://www.blinkist.com/{language}/nc/library"
        if not driver.current_url.rstrip('/') == library_url:
            driver.get(library_url)
        # wait for the page to load by checking we're no longer on the
        # login page (resilient to site redesigns)
        WebDriverWait(driver, 30).until(
            lambda d: "/nc/login" not in d.current_url
        )
    except TimeoutException:
        log.error("Error logging in: timed out waiting for library page")
        return False

    # login successful, store login cookies for future operations
    store_login_cookies(driver)
    return True


def get_categories(
    driver, language, specified_categories=None, ignored_categories=[]
):
    url_with_categories = f"https://www.blinkist.com/{language}/nc/login"
    driver.get(url_with_categories)
    categories_links = []

    # wait for page to be ready
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        log.error("Error loading page: timed out waiting for page ready")

    # click the discover dropdown to reveal categories links
    try:
        WebDriverWait(driver, 45).until(
                EC.presence_of_element_located(
                    (By.CLASS_NAME, "header-menu__trigger")
                )
            )
        categories_menu = driver.find_element(
            By.CLASS_NAME, "header-menu__trigger")
        categories_menu.click()
    except NoSuchElementException:
        log.warning("Could not find categories dropdown element")
        return
    except ElementNotInteractableException:
        # element not interactable
        log.debug("Found the dropdown element, but could not click it. "
                  "Using fallback JS code.")
        driver.execute_script(
            "document.querySelector('.header-menu__trigger').click()"
        )

    # find the categories links container
    categories_list = None
    categories_elements = ["discover-menu__categories", "category-list"]
    for classname in categories_elements:
        try:
            categories_list = driver.find_element(By.CLASS_NAME, classname)
            break
        except Exception:
            log.debug(
                "Could not find categories container element with class "
                f"'{classname}'")
    else:
        log.warning(
            "Could not find a categories container element")

    # parse the invidual category links
    categories_items = categories_list.find_elements(By.TAG_NAME, "li")
    for item in categories_items:
        link = item.find_element(By.TAG_NAME, "a")
        href = link.get_attribute("href")
        label = link.find_element(By.TAG_NAME, "span").get_attribute(
            "innerHTML")

        # Do not add this category if specific_categories is specified AND
        # the label doesn't contain anything specified there
        if specified_categories:
            if not list(
                filter(
                    lambda oc: oc.lower() in label.lower(),
                    specified_categories
                )
            ):
                continue
        # Do not add this category if the label contains any strings from
        # ignored_categories
        if list(
            filter(lambda ic: ic.lower() in label.lower(), ignored_categories)
        ):
            continue

        category = {
            "label": " ".join(label.split()).replace("&amp;", "&"), "url": href
        }
        categories_links.append(category)
    log.info(
        "Scraping categories: "
        f"{', '.join([c['label'] for c in categories_links])}"
    )
    return categories_links


def get_all_books_for_categories(driver, category):
    log.info(f"Getting all books for category {category['label']}...")
    books_links = []
    driver.get(category["url"] + "/books")
    books_items = driver.find_elements(By.CLASS_NAME, "letter-book-list__item")
    for item in books_items:
        href = item.get_attribute("href")
        books_links.append(href)
    log.info(f"Found {len(books_links)} books")
    return books_links


def get_all_books(driver, match_language):
    log.info("Getting all Blinkist books from sitemap...")
    all_books_links = []
    driver.get("https://www.blinkist.com/en/sitemap")

    selector = ".sitemap__section.sitemap__section--books a"
    if match_language:
        selector += f"[href$='{match_language}']"

    books_items = driver.find_elements(By.CSS_SELECTOR, selector)

    for item in books_items:
        href = item.get_attribute("href")
        all_books_links.append(href)
    log.info(f"Found {len(all_books_links)} books")
    return all_books_links


def get_daily_book_url(driver, language):
    driver.get(f"https://www.blinkist.com/{language}/nc/daily")
    daily_book_url = driver.find_element(
        By.CSS_SELECTOR, ".daily-book__infos a")
    if daily_book_url:
        return daily_book_url.get_attribute("href")
    else:
        return ""


def detect_needs_upgrade(driver):
    # check for re-direct to the upgrade page
    if driver.current_url.endswith('/nc/plans'):
        # needs subscription
        log.warn('Book is not available on the selected account. Exiting...')
        driver.close()
        log.info('Go Premium and get the best of Blinkist: '
                 'https://www.blinkist.com/nc/plans')
        # exit the scraper
        exit()


def scrape_chapters_from_page(driver):
    """Scrape chapter content from Blinkist reader using current Nuxt CSS selectors."""
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#__nuxt"))
        )
    except TimeoutException:
        log.debug("Nuxt root element not found, page may use a different layout")
        return []

    time.sleep(3)

    chapter_blocks = driver.find_elements(By.CSS_SELECTOR, "div.mb-8")
    scraped_chapters = []
    for block in chapter_blocks:
        title = ""
        try:
            title_el = block.find_element(By.CSS_SELECTOR, "div > h2 > span")
            title = title_el.text.strip()
        except NoSuchElementException:
            pass

        content = ""
        try:
            content_el = block.find_element(
                By.CSS_SELECTOR,
                "span.transition.text-r2.text-dark-grey > span"
            )
            content = content_el.get_attribute("innerHTML")
        except NoSuchElementException:
            pass

        if title or content:
            scraped_chapters.append({"title": title, "content": content})

    return scraped_chapters


def scrape_book_data(
    driver, book_url, match_language="", category={"label": "Uncategorized"},
    force=False
):
    if os.path.exists(get_book_dump_filename(book_url)) and not force:
        log.debug(
            f"Json dump for book {book_url} already exists, skipping "
            "scraping...")
        with open(get_book_dump_filename(book_url)) as f:
            return json.load(f), True

    log.info(f"Scraping book at {book_url}")

    # normalize URL to reader page format
    original_url = book_url
    if "/nc/reader/" not in book_url:
        book_url = book_url.replace("/reader/books/", "/nc/reader/")
        book_url = book_url.replace("/app/books/", "/nc/reader/")
        book_url = book_url.replace("/en/books/", "/en/nc/reader/")

    if not driver.current_url == book_url:
        driver.get(book_url)

    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        pass

    # if we were redirected, try the original URL as fallback
    actual_url = driver.current_url
    if "/nc/reader/" not in actual_url and book_url != original_url:
        log.debug(f"Redirected to {actual_url}, trying original URL")
        driver.get(original_url)
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script(
                    "return document.readyState") == "complete"
            )
        except TimeoutException:
            pass

    log.debug(f"Current URL after navigation: {driver.current_url}")

    detect_needs_upgrade(driver)

    # extract slug from URL
    slug = book_url.rstrip("/").split("/")[-1]

    # 1. Get book metadata from API
    book = None
    slug_response = requests.get(
        url=f"https://api.blinkist.com/v4/books/{slug}")
    if slug_response.status_code == 200:
        book_json = slug_response.json()
        book = book_json["book"]
        log.debug(f"Found book via API: {book['title']}")
    else:
        try:
            reader = driver.find_element(By.CLASS_NAME, "reader__container")
            book_id = reader.get_attribute("data-book-id")
            book_json = requests.get(
                url=f"https://api.blinkist.com/v4/books/{book_id}").json()
            book = book_json["book"]
        except NoSuchElementException:
            log.error(
                f"Could not find book data for {book_url}. "
                "The Blinkist API may have changed."
            )
            return None, False

    if not book:
        return None, False

    if match_language and book["language"] != match_language:
        log.warning(
            f"Book not available in the selected language ({match_language}), "
            "skipping scraping..."
        )
        return None, False

    book["title"] = sanitize_name(book["title"])
    book["author"] = sanitize_name(book["author"])

    # 2. Check if chapters already have content (free daily book)
    json_needs_content = False
    for chapter_json in book["chapters"]:
        if "text" not in chapter_json:
            json_needs_content = True
            break
        else:
            chapter_json["content"] = chapter_json.pop("text")

    if json_needs_content:
        # 3a. Try API chapter endpoints with auth cookies
        browser_cookies = {
            c["name"]: c["value"] for c in driver.get_cookies()
        }
        api_failures = 0
        for chapter_json in book["chapters"]:
            chapter_url = (
                f"https://api.blinkist.com/v4/books/{book['id']}"
                f"/chapters/{chapter_json['id']}"
            )
            log.debug(f"Fetching chapter from: {chapter_url}")
            try:
                ch_response = requests.get(
                    url=chapter_url, cookies=browser_cookies)
                if ch_response.status_code == 200:
                    ch_data = ch_response.json()
                    ch = ch_data.get("chapter", ch_data)
                    chapter_json["content"] = ch.get(
                        "text", ch.get("content", ""))
                    chapter_json["supplement"] = ch.get("supplement", "")
                else:
                    api_failures += 1
                    log.debug(
                        f"Chapter API returned {ch_response.status_code} "
                        f"for chapter {chapter_json['order_no']}")
            except Exception as e:
                api_failures += 1
                log.debug(f"Error fetching chapter content: {e}")

        if api_failures > 0 and api_failures < len(book["chapters"]):
            log.info(
                f"API fetch partially succeeded: "
                f"{len(book['chapters']) - api_failures}/{len(book['chapters'])} chapters"
            )

        # 3b. If API failed for all chapters, try page scraping
        if api_failures == len(book["chapters"]):
            log.warning(
                f"API chapter fetch failed for all {api_failures} chapters "
                f"in '{book['title']}', trying page scraping"
            )
            scraped = scrape_chapters_from_page(driver)
            if scraped:
                log.debug(
                    f"Page scraping found {len(scraped)} chapter blocks")
                for i, chapter_json in enumerate(book["chapters"]):
                    if i < len(scraped) and not chapter_json.get("content"):
                        chapter_json["content"] = (
                            scraped[i].get("content", ""))
                        if not chapter_json.get("title") and scraped[i].get("title"):
                            chapter_json["title"] = scraped[i]["title"]
            else:
                log.warning(
                    f"Page scraping found no content for '{book['title']}'. "
                    "Output files will contain metadata only."
                )

        # 4. Sanitize: ensure every chapter has content/supplement keys
        for chapter_json in book["chapters"]:
            if "content" not in chapter_json:
                chapter_json["content"] = ""
            if "supplement" not in chapter_json:
                chapter_json["supplement"] = ""

    # 5. Content validation
    chapters_with_content = sum(
        1 for ch in book["chapters"] if ch.get("content", "").strip()
    )
    total_chapters = len(book["chapters"])
    if chapters_with_content == 0:
        log.warning(
            f"Could not fetch content for any of {total_chapters} chapters in "
            f"'{book['title']}'. Output files will contain metadata only."
        )
    elif chapters_with_content < total_chapters:
        log.warning(
            f"Fetched content for {chapters_with_content}/{total_chapters} "
            f"chapters in '{book['title']}'"
        )
    else:
        log.info(
            f"Successfully fetched all {total_chapters} chapters "
            f"for '{book['title']}'"
        )

    book["category"] = category["label"]
    dump_book(book)
    return book, False


def dump_book(book_json):
    # dump the book's metadata in a json file within the dump folder
    filepath = get_book_dump_filename(book_json)
    if not os.path.exists(os.path.dirname(filepath)):
        os.makedirs(os.path.dirname(filepath))
    with open(filepath, "w") as outfile:
        json.dump(book_json, outfile, indent=4)
    return filepath


def scrape_book_audio(driver, book_json, language):
    # check if the book actually has audio blinks
    if not (book_json["is_audio"]):
        log.debug(
            f"Book {book_json['slug']} does not have audio blinks, "
            "skipping scraping audio..."
        )
        return False

    # clear out previous captured requests and restrict scope to the blinkist
    # site
    del driver.requests
    driver.scopes = [".*blinkist.*"]

    # navigate to the book's reader page which also contains the media player
    # for the first audio blink
    book_reader_url = (
        f'https://www.blinkist.com/{language}/nc/reader/{book_json["slug"]}')

    log.info(f"Scraping book audio at {book_reader_url}")
    driver.get(book_reader_url)

    # check for re-direct to the upgrade page
    detect_needs_upgrade(driver)

    # wait until the request to the audio endpoint is captured,
    # then its headers for future requests
    try:
        captured_request = driver.wait_for_request("audio", timeout=30)
        audio_request_headers = captured_request.headers
    except TimeoutException as ex:
        log.error("Could not capture an audio endpoint request")
        log.error(str(ex))
        return False

    audio_files = []
    error = False

    # using requests instead of urllib.request to fetch the audio seems to
    # trigger Cloudflare's captcha
    # see https://stackoverflow.com/questions/62684468
    # /pythons-requests-triggers-cloudflares-security-while-urllib-does-not
    import urllib.request
    import json
    import gzip

    # go through every chapter object in the book json data
    # and build a request to the audio endpoint using the book and chapter ID
    for chapter_json in book_json["chapters"]:
        time.sleep(1.0)
        api_url = (
            f"https://www.blinkist.com/api/books/{book_json['id']}"
            f"/chapters/{chapter_json['id']}/audio")
        log.debug(f"Fetching blink audio from: {api_url}")
        try:
            audio_request = urllib.request.Request(
                api_url, headers=audio_request_headers)
            audio_request_content = urllib.request.urlopen(
                audio_request).read()
            audio_request_json = json.loads(
                gzip.decompress(audio_request_content).decode("utf-8")
            )
            if "url" in audio_request_json:
                audio_url = audio_request_json["url"]
                audio_file = download_book_chapter_audio(
                    book_json, chapter_json["order_no"], audio_url
                )
                audio_files.append(audio_file)
            else:
                log.warning(
                    "Could not find audio url in request, aborting audio "
                    "scrape..."
                )
                error = True
                break
        except json.decoder.JSONDecodeError:
            log.error(f"Received malformed json data: {audio_request.text}")
            log.warning(
                "Could not find audio url in request, aborting audio "
                "scrape...")
            error = True
            break
        except Exception as e:
            log.error(f"Request timed out or other unexpected error: {e}")
            error = True
            break

    if not error:
        return audio_files
    else:
        log.error("Error processing audio url, aborting audio scrape...")
        return []


def download_book_chapter_audio(book_json, chapter_no, audio_url):
    filepath = get_book_pretty_filepath(book_json)
    filename = str(chapter_no) + ".m4a"
    audio_file = os.path.join(filepath, filename)
    if not os.path.exists(filepath):
        os.makedirs(filepath)
    if not os.path.exists(audio_file):
        log.info(
            f"Downloading audio file for blink {chapter_no} of "
            f"{book_json['slug']}..."
        )
        download_request = requests.get(audio_url)
        with open(audio_file, "wb") as outfile:
            outfile.write(download_request.content)
    else:
        log.debug(
            f"Audio for blink {chapter_no} already downloaded, "
            "skipping...")
    return audio_file


def download_book_cover_image(
    book_json, filename="_cover.jpg", size="640", type="1_1",
    alt_file="cover.jpg"
):
    """
    Downloads the cover image specified in 'book_json'.

    book_json -- dictionary object with book metadata.
    filename -- filename of the output files
    size -- the width of the image in pixels.
            The 'sizes' options (generally) are: 130, 250, 470, 640, 1080,
            and 1400.
    type -- the aspect ratio of for the cover image.
            The 'types' options (generally) are: '1_1', '2-2_1', and '3_4'.
    alt_file -- an identical file to the expected image, but with a
    different name.

    The default 'image_url' (used by the HTML output) is type: '3_4',
    size: 640.
    """

    # default cover image:
    # cover_img_url = book_json["image_url"]

    # variable size/resolution: (default is 640*640 px)
    cover_img_url_tmplt = book_json["images"]["url_template"]
    cover_img_url = cover_img_url_tmplt.replace(
        "%type%", type).replace("%size%", size)

    filepath = get_book_pretty_filepath(book_json)
    cover_img_file = os.path.join(filepath, filename)
    cover_img_alt_file = os.path.join(filepath, alt_file)
    if not os.path.exists(filepath):
        os.makedirs(filepath)
    if not os.path.exists(cover_img_file):
        # check if we have the "alternative" image file avaible
        if not os.path.exists(cover_img_alt_file):
            # download the image
            log.info(f'Downloading "{cover_img_url}" as "{filename}"')
            download_request = requests.get(cover_img_url)
            with open(cover_img_file, "wb") as outfile:
                outfile.write(download_request.content)
        else:
            # copy the image file
            log.debug(f"Copying {alt_file} as {filename}")
            copy_file(cover_img_alt_file, cover_img_file)
    else:
        log.debug(f"{filename} already exists, skipping...")
    return cover_img_file

