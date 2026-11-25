"""Local Markdown guide rendering; raw HTML is always escaped."""
import re
from html import escape, unescape
from urllib.parse import urlsplit

from markupsafe import Markup
from storage import DEFAULT_GUIDE, BusinessError, get_db
from vendor import mistune
from vendor.mistune.plugins.table import table
from vendor.mistune.plugins.formatting import strikethrough
from vendor.mistune.plugins.task_lists import task_lists


def guide_document():
    values = dict(get_db().execute("SELECT key,value FROM settings WHERE key IN ('guide','guide_format')").fetchall())
    return values.get('guide', DEFAULT_GUIDE), values.get('guide_format', 'text')


def guide_mode(value):
    if value not in {'text', 'markdown'}:
        raise BusinessError('请选择纯文本或 Markdown 格式')
    return value


def allowed_url(url, image=False):
    value = unescape(url).strip()
    if any(ord(c) < 32 for c in value) or '\\' in value:
        return False
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return False
    return scheme in ({'', 'http', 'https'} if image else {'', 'http', 'https', 'mailto', 'tel'}) and not value.startswith('//')


class GuideRenderer(mistune.HTMLRenderer):
    def link(self, text, url, title=None):
        if not allowed_url(url):
            return text
        return super().link(text, url, title)

    def image(self, text, url, title=None):
        if not allowed_url(url, image=True):
            return escape(unescape(text))
        width, alignment = 100, 'center'
        layout = re.fullmatch(r'width=(\d{1,3});align=(left|center|right)', title or '')
        if layout:
            width = max(10, min(100, int(layout[1])))
            alignment = layout[2]
        return (f'<span class="md-image md-image-{alignment}" style="width:{width}%">'
                f'<img src="{escape(url, quote=True)}" alt="{escape(unescape(text), quote=True)}" loading="lazy"></span>')


def render_guide(content, mode):
    guide_mode(mode)
    if mode == 'text':
        return Markup(escape(content))
    # A fresh parser per request avoids sharing mutable parsing state between workers.
    renderer = GuideRenderer(escape=True, allow_harmful_protocols=False)
    markdown = mistune.create_markdown(renderer=renderer, plugins=[table, strikethrough, task_lists])
    return Markup(markdown(content))
