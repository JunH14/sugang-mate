"""HTML syllabus text with explicit native checkbox/radio state preservation.

Only checked options are evidence of selected activity. Unlabelled controls or
script-managed widgets cannot be reconstructed from a saved static HTML file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class SyllabusHTMLParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.controls = {}
        self.labelled_ids = set()
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        self.parents[id(node)] = self.stack[-1]
        if tag == "label" and node.attrs.get("for"):
            self.labelled_ids.add(node.attrs["for"])
        if tag == "input" and node.attrs.get("id"):
            self.controls[node.attrs["id"]] = node
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    @staticmethod
    def is_choice(node):
        return node.tag == "input" and node.attrs.get("type", "").lower() in {"checkbox", "radio"}

    def descendants(self, node):
        for child in node.children:
            if isinstance(child, Node):
                yield child
                yield from self.descendants(child)

    def has_choice_label(self, node):
        if node.attrs.get("id") in self.labelled_ids:
            return True
        parent = self.parents.get(id(node))
        ancestor = parent
        while ancestor is not None:
            if ancestor.tag == "label":
                return True
            ancestor = self.parents.get(id(ancestor))
        if parent is not None:
            position = next(index for index, child in enumerate(parent.children) if child is node)
            for following in parent.children[position + 1:]:
                if isinstance(following, Node):
                    break
                if following.strip():
                    return True
        return False

    def render(self, node):
        if isinstance(node, str):
            return node
        if node.tag in {"script", "style", "noscript", "button"}:
            return ""
        if node.tag == "tr" and any(self.is_choice(child) and not self.has_choice_label(child)
                                    for child in self.descendants(node)):
            # Separate table cells do not establish which text labels a control.
            return "\n[선택 상태를 연결할 수 없는 양식 행 생략]\n"
        if node.tag == "br":
            return "\n"
        if node.tag == "label":
            control = self.controls.get(node.attrs.get("for"))
            if control is None:
                control = next((child for child in self.descendants(node) if self.is_choice(child)), None)
            if control is not None and self.is_choice(control):
                if "checked" not in control.attrs:
                    return "\n"
                label = "".join(self.render(child) for child in node.children if not isinstance(child, Node) or child.tag != "input")
                return "\n선택 확인: " + label.replace("선택 확인: ", "").strip() + "\n"
        if node.tag == "input":
            return ""
        if node.tag == "option" and "selected" not in node.attrs:
            return ""
        # A bare input followed by text is a common non-label form. Associate
        # only text siblings up to the next element; do not consume other fields.
        parts = []
        skip_choice_text = False
        for child in node.children:
            if isinstance(child, Node):
                if child.tag not in {"span", "b", "strong", "i", "em"}:
                    skip_choice_text = False
                if self.is_choice(child):
                    if child.attrs.get("id") in self.labelled_ids:
                        continue
                    skip_choice_text = "checked" not in child.attrs
                    if not skip_choice_text:
                        parts.append("\n선택 확인: ")
                    continue
                if not skip_choice_text:
                    parts.append(self.render(child))
            elif not skip_choice_text:
                parts.append(child)
        result = "".join(parts)
        if node.tag in {"p", "div", "tr", "table", "li", "h1", "h2", "h3", "h4", "h5", "h6", "select"}:
            result += "\n"
        elif node.tag in {"td", "th"}:
            result += " | "
        return result


def extract_static_html_text(raw: str) -> str:
    parser = SyllabusHTMLParser()
    parser.feed(raw)
    parser.close()
    return parser.render(parser.root)
