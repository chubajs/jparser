#!/bin/env python
#encoding=utf-8
import re
import lxml
import lxml.html
from lxml.etree import Comment



from urllib import parse as urlparse
from urllib.parse import urljoin

from .tags_util import clean_tags_only, clean_tags_hasprop, clean_tags_exactly, clean_tags, pick_listed_tags, clean_ainp_tags
from .region import Region

class PageModel(object):
    def __init__(self, page, url=""):
        self.page = page
        self._clean_page()
        self.doc = lxml.html.fromstring(self.page)
        self.url = url
        self.region = Region(self.doc)
        self.impurity_threshold = 30
        self.anchor_ratio_limit = 0.3
        self.stripper = re.compile(r'\s+')

    def _clean_page(self):
        for tag in ['style', 'script', 'sup', 'noscript']:  
                self.page = clean_tags(self.page, tag)

        self.otherlists = pick_listed_tags(self.page, 'section')
        self.page = clean_tags_hasprop(self.page, "div", "(display:.?none|comment|measure)")
        self.page = clean_tags_only(self.page, "(span|section|font|em|i)")

    def _clean_link_from_webarchive(self, link):
        # http://web.archive.org/web/20120510161402/http://www.mashable.com/tag/pinterest
        re.TRIM = r'http://web.archive.org/web/\d+/(.*)'
        return re.sub(re.TRIM, r'\1', link)

    def _handle_link(self, item):
        link = item.get('href', '')
        link = urljoin(self.url, link)
        anchor = item.text_content().strip()
        return f"[{anchor}]({link})"

    def _handle_img(self, item):
        for img_prop in ('original', 'file', 'data-original', 'src-info', 'data-src', 'src'):
            src = item.get(img_prop)
            if src:
                src = urljoin(self.url, src)
                break
        return src      

    def extract_content(self, region):
        for item in self.otherlists:
            region.append(item)
        
        # Simplified tag extraction
        items = region.xpath('.//text()|.//img|./table|./aside|.//a')
        tag_hist = {}
        for item in items:
            if  hasattr(item,'tag'):
                continue
            t = item.getparent().tag
            if t not in tag_hist:
                tag_hist[t] = 0
            tag_hist[t] += len(item.strip())
        winner_tag = None
        if len(tag_hist) > 0:

            for k, v in tag_hist.items():
                if not isinstance(k, str):
                    print("Unexpected key type:", type(k), k)


            winner_tag = max((c, k) for k, c in tag_hist.items() if isinstance(k, str))[1]

        contents = []
        for item in items:
            if not hasattr(item,'tag'):
                txt = item.strip()
                parent_tag = item.getparent().tag
                if  parent_tag != winner_tag \
                    and len(self.stripper.sub("",txt)) < self.impurity_threshold \
                    and parent_tag != 'li':
                    continue
                contents.append({"type":"text","data":txt})
#            elif item.tag == 'a':
#                print("link found: ", item.text_content().strip(), item.get('href', ''))
#                md_link = self._handle_link(item)
#                contents.append({"type": "link", "data": md_link})
            elif item.tag == 'img':
                img_src = self._handle_img(item)
                contents.append({"type": "image", "data": {"src": img_src}})
            elif item.tag == 'table':
                if winner_tag == 'td':
                    continue
                if item.xpath(".//p"):
                    continue
                if item != region:
                    for el in item.xpath(".//a"):
                        el.drop_tag()
                    table_s = lxml.html.tostring(item)
                    contents.append({"type":"html","data":table_s})
                else:
                    for sub_item in item.xpath("//td/text()"):
                        contents.append({"type":"text","data":sub_item})
            elif item.tag == 'aside':
                if item != region:
                    for el in item.xpath(".//a"):
                        el.drop_tag()
                    aside_s = lxml.html.tostring(item)
                    contents.append({"type":"html","data":aside_s})
            elif item.tag == 'img':
                for img_prop in ('original', 'file', 'data-original', 'src-info', 'data-src', 'src'):
                    src =  item.get(img_prop)
                    if src is not None:
                        break
                if self.url != "":
                    if not src.startswith("/") and not src.startswith("http") and not src.startswith("./"):
                        src = "/" + src
                    src = urllib.parse.urljoin(self.url, src, False)
                contents.append({"type":"image","data":{"src": src}})                  
            else:
                pass   
        return contents

    def extract_title(self):
        doc = self.doc
        tag_title = doc.xpath("/html/head/title/text()")
        s_tag_title = "".join(re.split(r'_|-',"".join(tag_title))[:1])
        title_candidates = doc.xpath('//h1/text()|//h2/text()|//h3/text()|//p[@class="title"]/text()')
        for c_title in title_candidates:
            c_title = c_title.strip()
            if c_title!="" and (s_tag_title.startswith(c_title) or s_tag_title.endswith(c_title)):
                return c_title
        sort_by_len_list = sorted((-1*len(x.strip()),x) for x in ([s_tag_title] + title_candidates))
        restitle = sort_by_len_list[0][1]
        if type(restitle)!=str:
            restitle = s_tag_title
        return restitle

    def extract(self):
        title = self.extract_title()
        region = self.region.locate()
        if region is None:
            return {'title': '', 'content': []}
        
        rm_tag_set = set([])

        # Handle anchor content ratio and drop if necessary
        for p_el in region.xpath(".//p|.//li"):
            child_links = p_el.xpath(".//a")
            count_p = len(" ".join(p_el.xpath(".//text()")))
            count_a = len(" ".join([a.text_content() for a in child_links]))
            if float(count_a) / (count_p + 1.0) > self.anchor_ratio_limit:
                for a in child_links:
                    a.drop_tag()  # Remove the anchor tags

        # Convert anchor tags to markdown format
        # Convert anchor tags to markdown format
        for a_el in region.xpath(".//a"):
            link = a_el.get('href', '')
            if link:  # ensure there is a href value
                link = urljoin(self.url, link)
                if a_el.xpath(".//img"):
                    anchor_text = "IMAGE"
                else:
                    anchor_text = a_el.text_content().strip()
                
                markdown_link = f"[{anchor_text}]({self._clean_link_from_webarchive(link)})"
                a_el.getparent().replace(a_el, lxml.html.fromstring(markdown_link))


        # Add tags to remove set
        for el in region.xpath(".//strong|//b"):
            rm_tag_set.add(el)

        # Drop tags but keep their content
        for el in rm_tag_set:
            el.drop_tag()

        content = self.extract_content(region)
        return {"title": title, "content": content}
