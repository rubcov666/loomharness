"""Read exact Java method definitions from one file, without a project index."""

import re


TYPES = {"class_declaration", "interface_declaration", "record_declaration",
         "enum_declaration", "annotation_type_declaration"}
MEMBERS = """[
    (method_declaration)
    (constructor_declaration)
    (compact_constructor_declaration)
    (annotation_type_element_declaration)
] @declaration"""


def has_owner(node, requested):
    owners = []
    parent = node.parent
    while parent and len(owners) < len(requested):
        if parent.type in TYPES:
            owners.append(parent.child_by_field_name("name").text.decode("utf-8"))
        elif parent.type == "class_body" and parent.parent.type in {"object_creation_expression", "enum_constant"}:
            owners.append("<anonymous>")
        parent = parent.parent
    return owners == requested[::-1]


def show(path, symbol):
    if path.suffix != ".java":
        raise ValueError("symbol supports Java files; use rg for other languages")
    names = symbol.split(".")
    if not all(re.fullmatch(r"(?:[^\W\d]|\$)[\w$]*", name) for name in names):
        raise ValueError("expected a method name, optionally qualified by its declaring type")
    try:
        from tree_sitter import Language, Parser, Query, QueryCursor
        import tree_sitter_java
    except ImportError as error:
        raise ValueError("Java symbol lookup needs tree-sitter==0.25.2 and tree-sitter-java==0.23.5 "
                         "in this interpreter; rg -n is available independently") from error
    raw = path.read_bytes()
    language = Language(tree_sitter_java.language())
    root = Parser(language).parse(raw).root_node
    if root.has_error:
        raise ValueError(f"cannot verify method boundaries in {path}; Java parse error")
    declarations = QueryCursor(Query(language, MEMBERS)).captures(root).get("declaration", ())
    method = names[-1].encode("utf-8")
    found = False
    for node in sorted(declarations, key=lambda member: member.start_byte):
        if node.child_by_field_name("name").text != method or not has_owner(node, names[:-1]):
            continue
        first = node.start_point.row + 1
        last = node.end_point.row + bool(node.end_point.column)
        print(f"{path}:{first}-{last}")
        print(raw[node.start_byte:node.end_byte].decode("utf-8"))
        found = True
    if not found:
        raise ValueError(f"no matching declaration for {symbol} in {path}")
    return 0
