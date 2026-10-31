from pathlib import Path

from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.template.loader_tags import ExtendsNode, IncludeNode

TEMPLATES_DIR = Path(settings.BASE_DIR) / "web" / "templates"


def test_included_and_extended_templates_exist() -> None:
    """Every template named literally by an include or extends tag exists.

    Django looks such a template up only when the tag renders, so a missing one fails just the pages and branches that
    reach it, such as an event's ticket links. Each template is parsed, and every tag naming a template by a string is
    looked up.
    """
    referenced = set()
    for path in TEMPLATES_DIR.rglob("*.html"):
        template = get_template(str(path.relative_to(TEMPLATES_DIR)))
        nodes = template.template.nodelist.get_nodes_by_type(IncludeNode)  # ty: ignore[unresolved-attribute]
        nodes += template.template.nodelist.get_nodes_by_type(ExtendsNode)  # ty: ignore[unresolved-attribute]
        for node in nodes:
            name = node.template.var if isinstance(node, IncludeNode) else node.parent_name.var
            if isinstance(name, str):
                referenced.add(name)

    missing = []
    for name in sorted(referenced):
        try:
            get_template(name)
        except TemplateDoesNotExist:
            missing.append(name)

    assert {"web/base.html", "web/icons/ticket.html"} <= referenced
    assert missing == []
