import os
import re
import subprocess
from html import unescape
from ebooklib import epub

# from utils import *
from utils import get_book_pretty_filename
from utils import get_book_pretty_filepath
from utils import is_installed
from utils import get_or_read_json
# from utils import get_book_short_pretty_filename

import logger

log = logger.get(f"blinkistscraper.{__name__}")


def generate_book_html(book_json_or_file, cover_img_file=False):
    book_json = get_or_read_json(book_json_or_file)
    filepath = get_book_pretty_filepath(book_json)
    filename = get_book_pretty_filename(book_json, ".html")
    html_file = os.path.join(filepath, filename)
    if os.path.exists(html_file):
        log.debug(f"Html file for {book_json['slug']} already exists, not "
                  "generating...")
        return html_file
    log.info(f"Generating .html for {book_json['slug']}")

    # open the book html template and replace every occurency of {{key}}
    # with the relevant parameter from the json file
    book_template_file = open(
        os.path.join(os.getcwd(), "templates", "book.html"), "r")
    book_template = book_template_file.read()
    book_html = book_template
    for key in book_json:
        book_html = book_html.replace(f"{{{key}}}", str(book_json[key]))

    if cover_img_file:
        # replace the online (https://blinkist) URL with a local (/.jpg) one
        cover_img_url = book_json["image_url"]
        book_html = book_html.replace(cover_img_url, cover_img_file)

    # when the special tag {__chapters__} is found, open the chapter template
    # file and do the same, then add the template chapter's html into the
    # book's html
    if "{__chapters__}" in book_template:
        chapters_html = []
        chapter_template_file = open(
            os.path.join(os.getcwd(), "templates", "chapter.html"), "r"
        )
        chapter_template = chapter_template_file.read()
        for chapter_json in book_json["chapters"]:
            chapter_html = chapter_template
            for chapter_key in chapter_json:
                # sanitize null keys (e.g. supplement)
                if not chapter_json[chapter_key]:
                    chapter_json[chapter_key] = ""
                chapter_html = chapter_html.replace(
                    f"{{{chapter_key}}}", str(chapter_json[chapter_key])
                )
            chapters_html.append(chapter_html)

    book_html = book_html.replace("{__chapters__}", "\n".join(chapters_html))
    book_html = book_html.replace("<p>&nbsp;</p>", "")

    # finally, export the finished book html
    if not os.path.exists(filepath):
        os.makedirs(filepath)
    with open(html_file, "w", encoding="utf-8") as outfile:
        outfile.write(book_html)
    return html_file


def generate_book_epub(book_json_or_file):
    book_json = get_or_read_json(book_json_or_file)
    filepath = get_book_pretty_filepath(book_json)
    filename = get_book_pretty_filename(book_json, ".epub")
    epub_file = os.path.join(filepath, filename)
    if os.path.exists(epub_file):
        log.debug(f"Epub file for {book_json['slug']} already exists, not "
                  "generating...")
        return epub_file
    log.info(f"Generating .epub for {book_json['slug']}")
    book = epub.EpubBook()

    # set metadata
    book.set_identifier(book_json["id"])
    book.set_title(book_json["title"])
    book.set_language("en")
    book.add_author(book_json["author"])
    book.add_metadata("DC", "description", book_json["about_the_book"])

    # add chapters
    chapters = []
    # to-do: add who is this for / intro section with cover image
    for chapter_json in book_json["chapters"]:
        chapter = epub.EpubHtml(
            title=chapter_json["title"],
            file_name=f"chapter_{chapter_json['order_no']}.xhtml",
            lang="hr",
        )

        title = chapter_json.get("title") or ""
        content = chapter_json.get("content") or ""
        supplement = chapter_json.get("supplement") or ""

        chapter.content = f"<h2>{title}</h2>" + content + supplement

        book.add_item(chapter)
        chapters.append(chapter)

    # define Table Of Contents
    book.toc = chapters

    # add default NCX and Nav file
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # define CSS style
    style = open(
        os.path.join(os.getcwd(), "templates", "epub.css"), "r").read()
    nav_css = epub.EpubItem(
        uid="style_nav", file_name="style/nav.css", media_type="text/css",
        content=style
    )
    book.add_item(nav_css)

    # basic spine
    book.spine = ["nav"] + chapters

    # write to the file
    if not os.path.exists(filepath):
        os.makedirs(filepath)
    epub.write_epub(epub_file, book, {})
    return epub_file


def generate_book_pdf(book_json_or_file, cover_img_file=False):
    book_json = get_or_read_json(book_json_or_file)
    filepath = get_book_pretty_filepath(book_json)
    filename = get_book_pretty_filename(book_json, ".pdf")
    pdf_file = os.path.join(filepath, filename)
    if os.path.exists(pdf_file):
        log.debug(f"Pdf file for {book_json['slug']} already exists, not "
                  "generating...")
        return pdf_file

    # generates the html file if it doesn't already exists
    html_file = os.path.join(
        get_book_pretty_filepath(book_json),
        get_book_pretty_filename(book_json, ".html")
    )
    if not os.path.exists(html_file):
        generate_book_html(book_json_or_file, cover_img_file)

    log.debug(f"Generating .pdf for {book_json['slug']}")

    # try weasyprint first (pip-installable, no external binary needed)
    try:
        from weasyprint import HTML as WeasyHTML
        WeasyHTML(filename=html_file).write_pdf(pdf_file)
        return pdf_file
    except ImportError:
        pass

    # fall back to wkhtmltopdf
    if is_installed("wkhtmltopdf"):
        subprocess.run(["wkhtmltopdf", "--quiet", html_file, pdf_file])
        return pdf_file

    log.warning(
        "No PDF backend available. Install one of:\n"
        "  pip install weasyprint  (recommended)\n"
        "  or install wkhtmltopdf and add it to PATH"
    )


def combine_audio(book_json, files, keep_blinks=False, cover_img_file=False):
    if not is_installed("ffmpeg"):
        log.warning(
            "ffmpeg needs to be installed and added to PATH to combine audio "
            "files"
        )
        return

    log.info(f"Combining audio files for {book_json['slug']}")
    filepath = get_book_pretty_filepath(book_json)
    filename = get_book_pretty_filename(book_json, ".m4a")

    files_list = os.path.abspath(os.path.join(filepath, "temp.txt"))
    combined_audio_file = os.path.abspath(os.path.join(filepath, "concat.m4a"))
    tagged_audio_file = os.path.abspath(os.path.join(filepath, filename))

    # ffmpeg fails on windows if the output filepath is longer than 260 chars
    # if len(tagged_audio_file) >= 260:
    #     log.warn("ffmpeg output file longer than 260 characters. Trying "
    #              "shorter filename...")
    #     tagged_audio_file = os.path.abspath(
    #         os.path.join(
    #             filepath, get_book_short_pretty_filename(book_json, ".m4a")))
    #     if len(tagged_audio_file) >= 260:
    #         log.warn("shorter filename still too long! Consider running "
    #                  "the script from a shorter path.")
    #         return

    with open(files_list, "w", encoding="utf-8") as outfile:
        for file in files:
            # escape any quotes for the ffmpeg concat's command file list
            sanitized_file = os.path.abspath(file).replace("'", "'\\''")
            outfile.write(f"file '{sanitized_file}'\n")
    subprocess.run([
        "ffmpeg", "-nostats", "-loglevel", "0", "-y",
        "-f", "concat", "-safe", "0",
        "-i", files_list, "-c", "copy", combined_audio_file
    ])
    tag_args = [
        "ffmpeg", "-nostats", "-loglevel", "0", "-y",
        "-i", combined_audio_file,
    ]
    if cover_img_file:
        tag_args += ["-i", cover_img_file, "-map", "0", "-map", "1",
                     "-disposition:v:0", "attached_pic"]
    tag_args += [
        "-c", "copy",
        "-metadata", f"title={book_json['title']}",
        "-metadata", f"artist={book_json['author']}",
        "-metadata", f"album={book_json['category']}",
        "-metadata", "genre=Blinkist",
        tagged_audio_file,
    ]
    subprocess.run(tag_args)

    # clean up files
    if os.path.exists(files_list):
        os.remove(files_list)
    if os.path.exists(combined_audio_file):
        os.remove(combined_audio_file)
    if not (keep_blinks):
        log.debug(
            f"Cleaning up individual audio files for {book_json['slug']}")
        for file in files:
            if os.path.exists(file):
                os.remove(os.path.abspath(file))


def strip_html_tags(html_content):
    if not html_content:
        return ""
    text = str(html_content)
    # convert block-level elements to markdown equivalents
    text = re.sub(r'<h[1-6][^>]*>(.*?)</h[1-6]>', r'### \1\n', text)
    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>',
                  lambda m: "> " + m.group(1).strip(), text, flags=re.DOTALL)
    # inline formatting
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text)
    # list items
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.DOTALL)
    # paragraphs to double newlines
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text, flags=re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text)
    # strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # decode HTML entities
    text = unescape(text)
    # normalize whitespace (collapse multiple blank lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def generate_book_markdown(book_json_or_file):
    book_json = get_or_read_json(book_json_or_file)
    filepath = get_book_pretty_filepath(book_json)
    filename = get_book_pretty_filename(book_json, ".md")
    md_file = os.path.join(filepath, filename)
    if os.path.exists(md_file):
        log.debug(f"Markdown file for {book_json['slug']} already exists, "
                  "not generating...")
        return md_file
    log.info(f"Generating .md for {book_json['slug']}")

    lines = []
    lines.append(f"# {book_json['title']}")
    lines.append("")
    lines.append(f"**Author:** {book_json['author']}")
    if book_json.get("category"):
        lines.append(f"**Category:** {book_json['category']}")
    lines.append("")

    if book_json.get("about_the_book"):
        lines.append("## About This Book")
        lines.append("")
        lines.append(strip_html_tags(book_json["about_the_book"]))
        lines.append("")

    if book_json.get("who_should_read"):
        lines.append("## Who Should Read It")
        lines.append("")
        lines.append(strip_html_tags(book_json["who_should_read"]))
        lines.append("")

    for chapter in book_json.get("chapters", []):
        title = chapter.get("title", f"Chapter {chapter.get('order_no', '')}")
        lines.append(f"## {title}")
        lines.append("")
        content = strip_html_tags(chapter.get("content", ""))
        if content:
            lines.append(content)
            lines.append("")
        supplement = strip_html_tags(chapter.get("supplement", ""))
        if supplement:
            lines.append(supplement)
            lines.append("")

    if book_json.get("about_the_author"):
        lines.append("## About the Author")
        lines.append("")
        lines.append(strip_html_tags(book_json["about_the_author"]))
        lines.append("")

    if not os.path.exists(filepath):
        os.makedirs(filepath)
    with open(md_file, "w", encoding="utf-8") as outfile:
        outfile.write("\n".join(lines))
    return md_file
