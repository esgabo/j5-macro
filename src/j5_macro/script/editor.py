"""Structured script edits (docs/contract.md, "Script management").

:func:`apply_edits` takes the YAML text of a script and a list of typed edits
(:class:`~j5_macro.protocol.payloads.InsertEdit` and friends) and returns the new text.
It is pure: it reads nothing but its arguments and touches no file.

The text goes through a ``ruamel.yaml`` round trip, so comments, key order, quoting and
flow or block style of everything the edits do not touch are kept. The few things the
round trip itself would reformat (extra spaces after a colon, say) are put back on every
line the edits left alone, so untouched lines stay byte for byte. Formatting choices:

- The indentation is detected from the file (mapping indent, and the indent and dash
  offset of block sequences); a file that mixes styles is normalised to the first one
  found. Without block sequences the default is the examples' style: 2-space mappings
  and ``- `` indented by 2 under its key.
- New steps are block mappings with ``id`` first; a new mapping or list whose values are
  all scalars (a step's parameters, ``input`` entries) is written in flow style
  (``{x: 1, y: 2}``), anything else in block style. A flow step list that receives a
  step (``steps: []``) becomes a block list.
- Lines are never wrapped, a leading ``---`` is kept, and line ends become ``\\n``. An
  end-of-line comment keeps its column when its line changes (``x: 1   # c``).
- ``set_meta`` and ``update`` change mappings in place: entries whose value does not
  change keep their formatting, removed ones go, new ones are added at their position.
- The comment lines right above a step (indented no deeper than the step) belong to it:
  they move and go away with it. Comments after the last entry of a block stay after
  that block when entries are added, removed or moved.
- Embedded images (``set_meta``'s ``images``) are block mappings whose base64 is a
  literal block (``|``) wrapped at 76 columns; a new ``images`` section goes at the end
  of the file, after ``steps``. An entry whose base64 only differs in whitespace is
  unchanged.

Ids are written: before any edit, every step without an id gets the id the loader
generates for it (:mod:`j5_macro.script.loader`, "ids"), so existing breakpoints by id
stay valid and the ids become stable. A step whose ``id`` is invalid keeps it (the
loader keeps reporting it) and is addressed by its generated id during the edits.
"""

from __future__ import annotations

import difflib
import io
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark, YAMLError
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    LiteralScalarString,
    PlainScalarString,
)
from ruamel.yaml.tokens import CommentToken

from j5_macro.protocol.errors import ErrorCode
from j5_macro.protocol.payloads import (
    DeleteEdit,
    Edit,
    InsertEdit,
    MoveEdit,
    Position,
    SetMetaEdit,
    UpdateEdit,
)
from j5_macro.script.images import FORMATS as IMAGE_FORMATS
from j5_macro.script.images import compact_base64, image_slots, wrap_base64
from j5_macro.script.loader import _TOP_LEVEL, ID_PATTERN, _parse_error, _plain
from j5_macro.steps.spec import StepLookup, StepSpec

COMMON_FIELDS = ("break", "timeout", "on_error", "input", "as")
"""Common step fields an ``update`` may change (``id`` is not one of them)."""

_ID_AND_COMMON = ("id", *COMMON_FIELDS)
_SCALARS = (str, int, float, bool, type(None))


class EditError(Exception):
    """An edit that cannot apply. ``index`` is the edit's position in the list, or None
    when the script itself cannot be edited (invalid YAML, not a mapping)."""

    def __init__(self, message: str, index: int | None = None, *,
                 code: ErrorCode = ErrorCode.VALIDATION_FAILED, line: int | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.index = index
        self.code = code
        self.line = line
        self.details = details


def apply_edits(text: str, edits: Sequence[Edit], *, lookup: StepLookup) -> str:
    """Apply ``edits`` in order to script ``text`` and return the new text.

    Raises :class:`EditError` for the first edit that cannot apply; nothing is applied
    then (the input text is never modified, there is no partial result).
    """
    text, yaml, tree = _open(text, lookup)
    tree.write_ids()
    for index, edit in enumerate(edits):
        try:
            tree.apply(edit)
        except _Refused as exc:
            raise EditError(f"edit {index} ({edit.op}): {exc}", index) from exc
    return _restore_untouched(text, _dump(yaml, yaml.load(text)), _dump(yaml, tree.data))


def rewrite_images(text: str, references: Mapping[str, str], *, lookup: StepLookup,
                   add: Mapping[str, Any] | None = None,
                   remove_section: bool = False) -> str:
    """The edits behind ``j5 pack`` and ``j5 unpack``: replace image parameters (a value
    as written, key of ``references``, becomes its value), add or replace entries of
    the ``images`` section (``add``), and drop that section (``remove_section``).

    Same round trip as :func:`apply_edits` (comments and formatting survive), except
    that no step id is written. Raises :class:`EditError` when ``text`` is not a YAML
    mapping."""
    text, yaml, tree = _open(text, lookup)
    tree.rewrite_references(references)
    if remove_section:
        tree.remove_header("images")
    elif add:
        tree.set_images(add, replace=False)
    return _restore_untouched(text, _dump(yaml, yaml.load(text)), _dump(yaml, tree.data))


def _open(text: str, lookup: StepLookup) -> tuple[str, YAML, _Tree]:
    """The text with ``\\n`` line ends, the YAML instance that keeps its formatting, and
    its tree; raises :class:`EditError` when it is not valid YAML or not a mapping."""
    text = text.replace("\r\n", "\n")
    indent = detect_indent(text)
    yaml = _yaml_for(text, indent)
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        info = _parse_error(exc)
        raise EditError(info.message, code=ErrorCode.PARSE_ERROR, line=info.line,
                        details=info.details) from exc
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise EditError("the script must be a mapping with a 'steps' list", line=1,
                        details={"reason": "not_a_mapping"})
    return text, yaml, _Tree(data, lookup, indent)


def _dump(yaml: YAML, data: Any) -> str:
    out = io.StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def _restore_untouched(original: str, baseline: str, edited: str) -> str:
    """Put back the original text of the lines the round trip reformats (``key:    v``
    loses its alignment, for instance) wherever the edits left them alone.

    ``baseline`` is the round trip of ``original`` without edits: a line it changed
    one-for-one is restored in ``edited`` where ``edited`` still has it unchanged."""
    if baseline == original:
        return edited
    old = original.splitlines(keepends=True)
    base = baseline.splitlines(keepends=True)
    restore: dict[int, str] = {}
    matcher = difflib.SequenceMatcher(a=base, b=old, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                line = old[j1 + k]
                restore[i1 + k] = line if line.endswith("\n") else line + "\n"
    if not restore:
        return edited
    lines = edited.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=base, b=lines, autojunk=False)
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                if i1 + k in restore:
                    lines[j1 + k] = restore[i1 + k]
    return "".join(lines)


# --- formatting -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Indent:
    mapping: int = 2
    sequence: int = 4
    """Column of a block sequence item's content, relative to its parent key."""
    offset: int = 2
    """Column of the ``-``, relative to its parent key."""


_KEY_LINE = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?"
                       r"(?P<key>[^\s#'\"-][^#]*?|'[^']*'|\"[^\"]*\")"
                       r"\s*:(?:\s+#.*)?\s*$")
"""A line that opens a block: ``key:`` (or ``- key:``) with nothing but a comment after."""
_DASH_LINE = re.compile(r"^(?P<indent>\s*)-(?P<gap>\s+)\S")


def detect_indent(text: str) -> Indent:
    """The block indentation of ``text``: the first sequence and the first nested mapping
    found under a key decide; anything not found keeps :class:`Indent`'s defaults."""
    mapping: int | None = None
    sequence: tuple[int, int] | None = None
    parent: int | None = None  # column of the key of the previous line, if it opens a block
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in ("---", "...") or stripped.startswith("--- "):
            parent = None
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        dash = _DASH_LINE.match(raw)
        if parent is not None:
            if dash is not None:
                if sequence is None and indent >= parent:
                    content = indent + 1 + len(dash.group("gap"))
                    sequence = (content - parent, indent - parent)
            elif mapping is None and indent > parent:
                mapping = indent - parent
        key = _KEY_LINE.match(raw)
        if key is not None:
            parent = indent + (len(key.group("dash")) if key.group("dash") else 0)
        else:
            parent = None
        if mapping is not None and sequence is not None:
            break
    result = Indent()
    if mapping is not None and 1 <= mapping <= 8:
        result = Indent(mapping, result.sequence, result.offset)
    if sequence is not None:
        seq, offset = sequence
        if offset >= 0 and seq >= offset + 2 and seq <= 10:
            result = Indent(result.mapping, seq, offset)
    return result


def _yaml_for(text: str, indent: Indent) -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 1_000_000
    yaml.indent(mapping=indent.mapping, sequence=indent.sequence, offset=indent.offset)
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("%"):
            continue
        yaml.explicit_start = stripped == "---" or stripped.startswith("--- ")
        break
    return yaml


def to_node(value: Any) -> Any:
    """A JSON value as a round-trip node: mappings and lists whose items are all scalars
    in flow style, the others in block style."""
    if isinstance(value, Mapping):
        node = CommentedMap()
        for key, item in value.items():
            node[str(key)] = to_node(item)
        if value and all(isinstance(v, _SCALARS) for v in value.values()):
            node.fa.set_flow_style()
        else:
            node.fa.set_block_style()
        return node
    if isinstance(value, list | tuple):
        seq = CommentedSeq(to_node(v) for v in value)
        if value and all(isinstance(v, _SCALARS) for v in value):
            seq.fa.set_flow_style()
        else:
            seq.fa.set_block_style()
        return seq
    return value


def _same(old: Any, new: Any) -> bool:
    """Equality that tells booleans from numbers (``true`` is not ``1``)."""
    if isinstance(old, Mapping) or isinstance(new, Mapping):
        return (isinstance(old, Mapping) and isinstance(new, Mapping)
                and set(old) == set(new) and all(_same(old[k], new[k]) for k in old))
    if isinstance(old, list) or isinstance(new, list):
        return (isinstance(old, list) and isinstance(new, list) and len(old) == len(new)
                and all(_same(a, b) for a, b in zip(old, new, strict=True)))
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool) and old == new
    if isinstance(old, float) != isinstance(new, float):
        return False
    return bool(old == new)


def _merge(old: Any, new: Any) -> Any:
    """The node to store for ``new`` where ``old`` was: ``old`` itself when the values are
    equal, ``old`` updated in place when both are mappings (so its style and comments
    stay), else a new node."""
    if _same(_plain(old), new):
        return old
    if isinstance(old, CommentedMap) and isinstance(new, Mapping):
        _merge_mapping(old, new)
        return old
    return to_node(new)


def _merge_mapping(node: CommentedMap, new: Mapping[str, Any]) -> None:
    """Make ``node`` hold exactly ``new``: missing keys go, changed values are replaced in
    their place, new keys are added at their position in ``new``."""
    for key in [k for k in node if str(k) not in new]:
        _map_delete(node, key)
    for position, (key, value) in enumerate(new.items()):
        existing = _key_in(node, key)
        if existing is not None:
            _map_set(node, existing, _merge(node[existing], value))
        else:
            _map_set(node, key, to_node(value), position)


# --- comments ---------------------------------------------------------------------------
#
# ruamel keeps the comment lines that follow a value on the last scalar before them, as
# one token: the rest of that line (its end-of-line comment, or just "\n") and then every
# following blank or comment line. So the comments above a step belong to the deepest last
# value of the step before it, and the comments after a mapping's last key to that key.
# Before editing, the comments above each step are moved to the step itself (a
# pre-comment of its list item), and every change that adds, removes or replaces the
# last entry of a block hands the comments that follow it to the new last entry.

_ANY_COLUMN = 1 << 30
Spot = tuple[Any, Any]
"""``(CommentedMap, key)`` or ``(CommentedSeq, index)``: where a comment token lives."""


def _key_in(node: CommentedMap, name: str) -> Any | None:
    return next((k for k in node if str(k) == name), None)


def _is_block(value: Any) -> bool:
    return (isinstance(value, CommentedMap | CommentedSeq) and len(value) > 0
            and not value.fa.flow_style())


def _last_key(container: CommentedMap | CommentedSeq) -> Any:
    return list(container)[-1] if isinstance(container, CommentedMap) else len(container) - 1


def _spot_after(owner: Any, key: Any) -> Spot:
    """Where the comments that follow ``owner[key]`` live: on its deepest last scalar."""
    value = owner[key]
    while _is_block(value):
        owner, key = value, _last_key(value)
        value = owner[key]
    return owner, key


def _end_spot(container: Any) -> Spot | None:
    if not _is_block(container):
        return None
    return _spot_after(container, _last_key(container))


def _slot(owner: Any) -> int:
    return 2 if isinstance(owner, CommentedMap) else 0


def _column(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _column_of(container: Any, key: Any) -> int:
    try:
        if isinstance(container, CommentedMap):
            return int(container.lc.key(key)[1])
        return int(container.lc.item(key)[1])
    except (AttributeError, KeyError, TypeError, IndexError):
        return _ANY_COLUMN


def _first_column(container: Any) -> int:
    keys = list(container) if isinstance(container, CommentedMap) else range(len(container))
    return min((_column_of(container, k) for k in keys), default=_ANY_COLUMN)


def _take_lines(spot: Spot | None, max_column: int) -> list[str]:
    """Remove and return the trailing blank and comment lines kept at ``spot`` that are
    indented at most ``max_column`` (deeper comments stay with what they follow)."""
    if spot is None:
        return []
    owner, key = spot
    entry = owner.ca.items.get(key)
    slot = _slot(owner)
    if not entry or len(entry) <= slot or entry[slot] is None:
        return []
    token = entry[slot]
    head, *rest = token.value.splitlines(keepends=True) or [""]
    cut = len(rest)
    while cut:
        stripped = rest[cut - 1].strip()
        if stripped and (not stripped.startswith("#") or _column(rest[cut - 1]) > max_column):
            break
        cut -= 1
    taken = rest[cut:]
    if taken:
        kept = head + "".join(rest[:cut])
        if kept == "\n":
            entry[slot] = None
        else:
            token.value = kept
    return taken


def _give_lines(spot: Spot | None, lines: list[str]) -> None:
    """Append comment lines after the value at ``spot``."""
    if spot is None or not lines:
        return
    owner, key = spot
    slot = _slot(owner)
    entry = owner.ca.items.setdefault(key, [None, None, None, None])
    while len(entry) <= slot:
        entry.append(None)
    text = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    token = entry[slot]
    if token is None:
        entry[slot] = CommentToken("\n" + text, CommentMark(0), None)
    else:
        value = token.value
        token.value = (value if value.endswith("\n") else value + "\n") + text


def _comment_tokens(lines: list[str]) -> list[CommentToken]:
    return [CommentToken(line.lstrip(" ") if line.strip() else "\n",
                         CommentMark(_column(line)), None) for line in lines]


def _lift_comments(seq: CommentedSeq) -> None:
    """Make the comment lines above each item (after the first) a pre-comment of it."""
    for index in range(len(seq) - 1):
        lines = _take_lines(_end_spot(seq[index]), _column_of(seq, index + 1))
        if lines:
            entry = seq.ca.items.setdefault(index + 1, [None, None, None, None])
            entry[1] = _comment_tokens(lines) + list(entry[1] or [])


def _map_set(node: CommentedMap, key: Any, value: Any, position: int | None = None) -> None:
    """Set ``node[key]``; a new key goes at ``position`` (the end by default). The
    comments that followed the replaced value, or the mapping, stay where they were."""
    existing = _key_in(node, str(key))
    if existing is not None:
        old = node[existing]
        if old is value:
            return
        lines = (_take_lines(_spot_after(node, existing), _ANY_COLUMN)
                 if _is_block(old) or _is_block(value) else [])
        node[existing] = value
        _give_lines(_spot_after(node, existing), lines)
        return
    place = len(node) if position is None else min(position, len(node))
    lines = []
    if place == len(node) and not node.fa.flow_style():
        lines = _take_lines(_end_spot(node), _first_column(node))
    node.insert(place, key, value)
    _give_lines(_spot_after(node, key), lines)


def _map_delete(node: CommentedMap, key: Any) -> None:
    """Delete ``node[key]``; the comment lines after it move to the entry before it."""
    keys = list(node)
    index = keys.index(key)
    lines: list[str] = []
    if not node.fa.flow_style():
        lines = _take_lines(_spot_after(node, key), _ANY_COLUMN)
    del node[key]
    if index > 0:
        _give_lines(_spot_after(node, keys[index - 1]), lines)


def _seq_insert(seq: CommentedSeq, index: int, node: Any,
                entry: list[Any] | None = None) -> None:
    """Insert ``node`` with its comment ``entry``; a list in flow style turns block."""
    if seq.fa.flow_style():
        seq.fa.set_block_style()
    lines: list[str] = []
    if seq and index == len(seq):
        lines = _take_lines(_end_spot(seq), _first_column(seq))
    seq.insert(index, node)
    if entry is not None:
        seq.ca.items[index] = entry
    _give_lines(_end_spot(seq), lines)


def _seq_remove(seq: CommentedSeq, node: Any) -> list[Any] | None:
    """Remove ``node`` and return its comment entry (the comments above it go with it)."""
    index = _index_of(seq, node)
    lines: list[str] = []
    if index == len(seq) - 1 and len(seq) > 1:
        lines = _take_lines(_end_spot(seq), _first_column(seq))
    entry: list[Any] | None = seq.ca.items.get(index)
    del seq[index]
    _give_lines(_end_spot(seq), lines)
    return entry


# --- the tree ---------------------------------------------------------------------------


class _Refused(Exception):
    """An edit that cannot apply; the message says why."""


@dataclass(eq=False, slots=True)
class _Ref:
    """A step found in the tree: its mapping, the list holding it and its id."""

    node: CommentedMap
    seq: CommentedSeq
    id: str | None
    type: str | None
    spec: StepSpec | None
    explicit: bool


def _type_key(node: Mapping[Any, Any]) -> Any | None:
    keys = [k for k in node if k not in _ID_AND_COMMON]
    return keys[0] if len(keys) == 1 else None


def _index_of(seq: CommentedSeq, node: Any) -> int:
    return next(i for i, item in enumerate(seq) if item is node)


def _token_column(token: CommentToken) -> int:
    try:
        return int(token.start_mark.column)
    except (AttributeError, TypeError):
        return 0


class _Tree:
    def __init__(self, data: CommentedMap, lookup: StepLookup, indent: Indent) -> None:
        self.data = data
        self.lookup = lookup
        self.indent = indent
        self._new_blocks: dict[int, tuple[CommentedMap, Any]] = {}
        """Blocks created by a position (a missing ``else``), by ``id()``: the mapping
        and the key of the block before it, whose trailing comments the first step put
        in the new block takes over."""
        lists: dict[int, CommentedSeq] = {}
        steps = data.get("steps")
        if isinstance(steps, CommentedSeq):
            lists[id(steps)] = steps
        for ref in self._walk(steps):
            lists.setdefault(id(ref.seq), ref.seq)
        for seq in lists.values():
            _lift_comments(seq)

    # --- walking and ids --------------------------------------------------------------

    def _walk(self, seq: Any) -> Iterator[_Ref]:
        """Every step under ``seq`` in document order, the way the loader walks them."""
        if not isinstance(seq, CommentedSeq):
            return
        for item in seq:
            if not isinstance(item, CommentedMap):
                continue
            raw_id = item.get("id")
            explicit = isinstance(raw_id, str) and bool(ID_PATTERN.match(raw_id))
            key = _type_key(item)
            type_name = None if key is None else str(key)
            spec = None if type_name is None else self.lookup(type_name)
            yield _Ref(item, seq, str(raw_id) if explicit else None, type_name, spec,
                       explicit)
            if spec is not None and isinstance(item[key], Mapping):
                for slot, value in item[key].items():
                    if str(slot) in spec.blocks:
                        yield from self._walk(value)

    def refs(self) -> list[_Ref]:
        """Every step, with the loader's generated ids filled in."""
        refs = list(self._walk(self.data.get("steps")))
        taken = {ref.id for ref in refs if ref.id is not None}
        position: dict[str, int] = {}
        for ref in refs:
            if ref.type is None:
                continue
            position[ref.type] = position.get(ref.type, 0) + 1
            if ref.explicit:
                continue
            n = position[ref.type]
            candidate = f"{ref.type}_{n}"
            suffix = 1
            while candidate in taken:
                suffix += 1
                candidate = f"{ref.type}_{n}_{suffix}"
            taken.add(candidate)
            ref.id = candidate
        return refs

    def write_ids(self) -> None:
        """Write the generated id of every step that has no ``id`` key."""
        for ref in self.refs():
            if ref.id is not None and not ref.explicit and "id" not in ref.node:
                ref.node.insert(0, "id", ref.id)

    def find(self, step_id: str) -> _Ref:
        ref = next((r for r in self.refs() if r.id == step_id), None)
        if ref is None:
            raise _Refused(f"unknown step id {step_id!r}")
        return ref

    def _new_id(self, type_name: str, taken: set[str]) -> str:
        """``<type>_<n>``, ``n`` starting at the number of steps of that type plus one."""
        n = 1 + sum(1 for r in self.refs() if r.type == type_name)
        while f"{type_name}_{n}" in taken:
            n += 1
        return f"{type_name}_{n}"

    # --- positions --------------------------------------------------------------------

    def _top_level(self) -> CommentedSeq:
        steps = self.data.get("steps")
        if steps is None:
            steps = CommentedSeq()
            _map_set(self.data, "steps", steps)
        if not isinstance(steps, CommentedSeq):
            raise _Refused("the script's 'steps' is not a list")
        return steps

    def resolve(self, to: Position) -> tuple[CommentedSeq, int]:
        """The list and the index a position names. A missing ``else`` (or top-level
        ``steps``) is created."""
        if to.after_step_id is not None:
            ref = self.find(to.after_step_id)
            return ref.seq, _index_of(ref.seq, ref.node) + 1
        if to.parent_step_id is None:
            seq = self._top_level()
        else:
            assert to.block is not None
            seq = self._block(self.find(to.parent_step_id), to.block)
        if to.index is None:
            return seq, len(seq)
        if to.index > len(seq):
            raise _Refused(f"index {to.index} is past the end of the list "
                           f"({len(seq)} steps)")
        return seq, to.index

    def _block(self, ref: _Ref, block: str) -> CommentedSeq:
        if ref.spec is None or block not in ref.spec.blocks:
            raise _Refused(f"step {ref.id!r} ({ref.type}) has no block {block!r}")
        body = ref.node[_type_key(ref.node)]
        if not isinstance(body, CommentedMap):
            raise _Refused(f"step {ref.id!r} has no parameters mapping to hold {block!r}")
        existing = _key_in(body, block)
        if existing is not None and body[existing] is not None:
            seq = body[existing]
            if not isinstance(seq, CommentedSeq):
                raise _Refused(f"block {block!r} of step {ref.id!r} is not a list")
            return seq
        if existing is None and block in ref.spec.required_blocks:
            raise _Refused(f"step {ref.id!r} has no {block!r} block")
        seq = CommentedSeq()
        seq.fa.set_block_style()
        if existing is not None:  # `else:` with nothing after it
            body[existing] = seq
            return seq
        # Created right after the blocks declared before it, when they are there.
        keys = list(body)
        earlier = ref.spec.blocks[:ref.spec.blocks.index(block)]
        place = max((i + 1 for i, k in enumerate(keys) if str(k) in earlier),
                    default=len(keys))
        if place == len(keys) and keys and not body.fa.flow_style():
            self._new_blocks[id(seq)] = (body, keys[-1])
        body.insert(place, block, seq)
        return seq

    def _put(self, seq: CommentedSeq, index: int, node: CommentedMap,
             entry: list[Any] | None = None) -> None:
        before = self._new_blocks.pop(id(seq), None)
        if before is None:
            _seq_insert(seq, index, node, entry)
            return
        # A new last block: the comments after the block before it now follow it.
        body, key = before
        lines = _take_lines(_spot_after(body, key), _column_of(body, key))
        _seq_insert(seq, index, node, entry)
        _give_lines(_end_spot(seq), lines)

    # --- edits ------------------------------------------------------------------------

    def apply(self, edit: Edit) -> None:
        if isinstance(edit, InsertEdit):
            self.insert(edit)
        elif isinstance(edit, UpdateEdit):
            self.update(edit)
        elif isinstance(edit, MoveEdit):
            self.move(edit)
        elif isinstance(edit, DeleteEdit):
            ref = self.find(edit.step_id)
            _seq_remove(ref.seq, ref.node)
        elif isinstance(edit, SetMetaEdit):
            self.set_meta(edit)
        else:  # pragma: no cover - the payload union is closed
            raise _Refused(f"unknown edit {edit!r}")

    def insert(self, edit: InsertEdit) -> None:
        taken = {r.id for r in self.refs() if r.id is not None}
        node = self._new_step(edit.step, taken, "step")
        seq, index = self.resolve(edit.to)
        self._put(seq, index, node)

    def _new_step(self, raw: Any, taken: set[str], where: str) -> CommentedMap:
        """A step node from its YAML-shaped mapping, with ids for it and for the steps of
        its blocks."""
        if not isinstance(raw, Mapping):
            raise _Refused(f"{where} must be a mapping with a step type key")
        fields = {str(k): v for k, v in raw.items()
                  if not (k in _ID_AND_COMMON and v is None)}
        types = [k for k in fields if k not in _ID_AND_COMMON]
        if len(types) != 1:
            raise _Refused(f"{where} must have exactly one step type key, got "
                           f"{', '.join(types) or 'none'}")
        type_name = types[0]
        spec = self.lookup(type_name)
        if spec is None:
            raise _Refused(f"{where}: unknown step type {type_name!r}")
        step_id = fields.get("id")
        if step_id is not None:
            if not isinstance(step_id, str) or not ID_PATTERN.match(step_id):
                raise _Refused(f"{where}: invalid id {step_id!r}: use letters, digits, '_' "
                               "or '-', starting with a letter or '_'")
            if step_id in taken:
                raise _Refused(f"{where}: id {step_id!r} is already used")
        else:
            step_id = self._new_id(type_name, taken)
        taken.add(step_id)

        node = CommentedMap()
        node["id"] = step_id
        for key, value in fields.items():
            if key == type_name:
                node[key] = self._new_body(spec, value, taken, f"{where}.{key}")
            elif key != "id":
                node[key] = to_node(value)
        node.fa.set_block_style()
        return node

    def _new_body(self, spec: StepSpec, value: Any, taken: set[str], where: str) -> Any:
        if not isinstance(value, Mapping) or not any(k in spec.blocks for k in value):
            return to_node(value)
        body = CommentedMap()
        for key, item in value.items():
            if key in spec.blocks:
                children = [] if item is None else item
                if not isinstance(children, list):
                    raise _Refused(f"{where}.{key} must be a list of steps")
                seq = CommentedSeq(self._new_step(child, taken, f"{where}.{key}[{i}]")
                                   for i, child in enumerate(children))
                seq.fa.set_block_style()
                body[str(key)] = seq
            else:
                body[str(key)] = to_node(item)
        body.fa.set_block_style()
        return body

    def update(self, edit: UpdateEdit) -> None:
        ref = self.find(edit.step_id)
        key = _type_key(ref.node)
        if edit.params:
            if key is None:
                raise _Refused(f"step {ref.id!r} has no single step type key")
            if ref.spec is None:
                raise _Refused(f"step {ref.id!r} has the unknown type {ref.type!r}")
            blocks = sorted(set(edit.params) & set(ref.spec.blocks))
            if blocks:
                raise _Refused(f"{', '.join(blocks)}: blocks are not parameters; change "
                               "their steps with insert, move and delete")
            _map_set(ref.node, key, self._updated_body(ref, ref.spec, ref.node[key],
                                                       edit.params))
        for name in edit.common or {}:
            if name not in COMMON_FIELDS:
                raise _Refused(f"{name!r} is not a common field; expected one of "
                               f"{', '.join(COMMON_FIELDS)}")
        for name, value in (edit.common or {}).items():
            _set_or_delete(ref.node, name, value)

    @staticmethod
    def _updated_body(ref: _Ref, spec: StepSpec, body: Any,
                      params: Mapping[str, Any]) -> Any:
        if isinstance(body, CommentedMap):
            was_empty = not body
            for name, value in params.items():
                _set_or_delete(body, name, value)
            if was_empty and body:  # `{}` gets the style of a new mapping
                _style_like_new(body)
            return body
        if body is not None and not isinstance(body, _SCALARS):
            raise _Refused(f"the parameters of step {ref.id!r} are not a mapping")
        shorthand = spec.shorthand
        current = CommentedMap()
        if body is not None:
            if shorthand is None:
                raise _Refused(f"step {ref.id!r} has a scalar value but {ref.type!r} has no "
                               "short form")
            current[shorthand] = body
        for name, value in params.items():
            _set_or_delete(current, name, value)
        # The short form stays while the step has nothing but its shorthand parameter.
        if (body is not None and list(current) == [shorthand]
                and isinstance(current[shorthand], _SCALARS)):
            return current[shorthand]
        if body is None and not current:
            return None
        _style_like_new(current)
        return current

    def move(self, edit: MoveEdit) -> None:
        ref = self.find(edit.step_id)
        to = edit.to
        if to.after_step_id == ref.id:
            return  # right after itself: it stays where it is
        target = to.after_step_id or to.parent_step_id
        members = {id(r.node) for r in self._walk(CommentedSeq([ref.node]))}
        inside = {r.id for r in self.refs() if id(r.node) in members}
        if target is not None and target in inside:
            raise _Refused(f"step {ref.id!r} cannot be moved into itself or its own blocks")
        before = self._column(ref.seq)
        entry = _seq_remove(ref.seq, ref.node)
        seq, index = self.resolve(to)
        if entry is not None and len(entry) > 1 and entry[1]:
            # The comment lines above the step follow it to its new depth.
            shift = self._column(seq) - before
            entry[1] = [CommentToken(t.value, CommentMark(max(0, _token_column(t) + shift)),
                                     None) for t in entry[1]]
        self._put(seq, index, ref.node, entry)

    def _column(self, seq: CommentedSeq) -> int:
        """The column of the steps of ``seq`` in the saved text (as the dumper indents)."""
        if seq is self.data.get("steps"):
            return self.indent.sequence
        parent = next(r for r in self.refs() if r.spec is not None
                      and isinstance(body := r.node[_type_key(r.node)], CommentedMap)
                      and any(v is seq for v in body.values()))
        return self._column(parent.seq) + self.indent.mapping + self.indent.sequence

    def set_meta(self, edit: SetMetaEdit) -> None:
        if edit.name is not None:
            self._set_header("name", edit.name)
        if edit.capture_scale is not None:
            self._set_header("capture_scale", edit.capture_scale)
        for key, value in (("input", edit.input), ("params", edit.params),
                           ("vars", edit.vars), ("images", edit.images)):
            if value is None:
                continue
            if not value:
                self.remove_header(key)  # an empty mapping removes the section
            elif key == "images":
                self.set_images(value, replace=True)
            else:
                self._set_header(key, value)

    def remove_header(self, key: str) -> None:
        existing = _key_in(self.data, key)
        if existing is not None:
            _map_delete(self.data, existing)

    def _set_header(self, key: str, value: Any) -> None:
        existing = _key_in(self.data, key)
        if existing is not None:
            _map_set(self.data, existing, _merge(self.data[existing], value))
            return
        node = to_node(value)
        if isinstance(node, CommentedMap):
            node.fa.set_block_style()  # header sections are blocks, one entry per line
        self._place_header(key, node)

    def _place_header(self, key: str, node: Any) -> None:
        """Add a new top-level section before the first section that follows it in
        the format's order (``images`` goes last)."""
        later = _TOP_LEVEL[_TOP_LEVEL.index(key) + 1:]
        place = next((i for i, k in enumerate(self.data) if k in later), len(self.data))
        _map_set(self.data, key, node, place)

    # --- embedded images --------------------------------------------------------------

    def set_images(self, value: Mapping[str, Any], *, replace: bool) -> None:
        """Write entries of the ``images`` section. With ``replace`` the section holds
        exactly ``value``; otherwise ``value``'s entries are added or replaced and the
        others stay. An entry equal to the one in the file (base64 compared without
        whitespace) keeps its formatting."""
        existing = _key_in(self.data, "images")
        current = None if existing is None else self.data[existing]
        if not isinstance(current, CommentedMap):
            node = CommentedMap()
            for name, entry in value.items():
                node[str(name)] = image_entry_node(entry)
            node.fa.set_block_style()
            if existing is not None:
                _map_set(self.data, existing, node)
            else:
                self._place_header("images", node)
            return
        if replace:
            for key in [k for k in current if str(k) not in value]:
                _map_delete(current, key)
        for position, (name, entry) in enumerate(value.items()):
            key = _key_in(current, str(name))
            if key is None:
                current.fa.set_block_style()  # literal blocks cannot go in a flow mapping
                _map_set(current, str(name), image_entry_node(entry),
                         position if replace else None)
            elif not _same_image(current[key], entry):
                current.fa.set_block_style()
                _map_set(current, key, image_entry_node(entry))

    def rewrite_references(self, references: Mapping[str, str]) -> int:
        """Replace every image parameter whose value (as written) is a key of
        ``references`` with its value; returns how many were replaced."""
        count = 0
        for ref in self._walk(self.data.get("steps")):
            if ref.spec is None:
                continue
            key = _type_key(ref.node)
            body = ref.node[key]
            if isinstance(body, CommentedMap):
                for mapping, slot, _ in list(image_slots(body, ref.spec)):
                    new = references.get(str(mapping[slot]))
                    if new is not None:
                        _map_set(cast(CommentedMap, mapping), slot, _image_value(new))
                        count += 1
            elif (isinstance(body, str) and ref.spec.shorthand == "image"
                    and (new := references.get(str(body))) is not None):
                _map_set(ref.node, key, _image_value(new))
                count += 1
        return count


def image_entry_node(entry: Any) -> Any:
    """An ``images`` entry as a block mapping whose base64 is a literal block (``|``)
    wrapped at 76 columns, so diffs stay line-based."""
    if not isinstance(entry, Mapping):
        return to_node(entry)
    node = CommentedMap()
    for key, value in entry.items():
        if str(key) in IMAGE_FORMATS and isinstance(value, str):
            wrapped = wrap_base64(value)
            node[str(key)] = LiteralScalarString(wrapped) if wrapped else ""
        else:
            node[str(key)] = to_node(value)
    node.fa.set_block_style()
    return node


def _same_image(old: Any, new: Any) -> bool:
    """Whether an ``images`` entry in the file already holds ``new``."""
    plain = _plain(old)
    if not isinstance(plain, Mapping) or not isinstance(new, Mapping):
        return _same(plain, new)
    if set(plain) != {str(k) for k in new}:
        return False
    for key, value in new.items():
        before = plain[str(key)]
        if str(key) in IMAGE_FORMATS and isinstance(value, str) and isinstance(before, str):
            if compact_base64(before) != compact_base64(value):
                return False
        elif not _same(before, value):
            return False
    return True


def _image_value(value: str) -> Any:
    """A reference is double-quoted (a bare ``#`` would start a comment); a path is
    written plain when YAML allows it. (A bare ``str`` would inherit the style of the
    value it replaces.)"""
    if value.startswith("#"):
        return DoubleQuotedScalarString(value)
    return PlainScalarString(value)


def _style_like_new(node: CommentedMap) -> None:
    """Flow style when every value is a scalar, block style otherwise (see to_node)."""
    if node and all(isinstance(_plain(v), _SCALARS) for v in node.values()):
        node.fa.set_flow_style()
    else:
        node.fa.set_block_style()


def _set_or_delete(node: CommentedMap, name: str, value: Any) -> None:
    """``None`` deletes ``node[name]``; anything else sets it, keeping what is unchanged."""
    existing = _key_in(node, name)
    if value is None:
        if existing is not None:
            _map_delete(node, existing)
    elif existing is not None:
        _map_set(node, existing, _merge(node[existing], value))
    else:
        _map_set(node, name, to_node(value))

