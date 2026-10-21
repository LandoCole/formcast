import pytest

from formcast.errors import FormcastError
from formcast.pagemap.model import FormMap, PageMap, TextLine, Word
from formcast.sandbox import ExecResult, Sandbox
from formcast.schema import flatten_schema
from formcast.template.model import Field, FormInfo, Template

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "married": {"type": "boolean"}},
}
NAME_LINE = (72.0, 88.0, 105.0, 101.0)


def _form_map() -> FormMap:
    page = PageMap(
        index=0,
        width=612,
        height=792,
        rotation=0,
        kind="digital",
        text_source="pdf",
        words=[Word(text="Name:", rect=NAME_LINE, line_id=0, source="pdf")],
        lines=[TextLine(id=0, text="Name:", rect=NAME_LINE, word_ids=[0], font_size=12)],
        rules=[],
        widgets=[],
    )
    return FormMap(pages=[page], fingerprint="fp")


def _template(field_ids=()) -> Template:
    return Template(
        template_id="t1",
        schema=SCHEMA,
        form=FormInfo(fingerprint="fp", page_count=1, page_sizes=[(612, 792)], kinds=["digital"]),
        fields=[Field(id=i, type="text", page=0, rect=(10, 10, 50, 20)) for i in field_ids],
    )


@pytest.fixture
def sandbox():
    with Sandbox() as sb:
        sb.start(_form_map(), flatten_schema(SCHEMA), _template())
        yield sb


def _ok(result: ExecResult) -> ExecResult:
    assert result.error is None, result.error
    assert not result.timed_out
    return result


# --- persistence and output ---


def test_namespace_persists_across_execs(sandbox):
    _ok(sandbox.exec("x = 1"))
    assert _ok(sandbox.exec("print(x + 1)")).stdout == "2\n"


def test_user_print_cannot_corrupt_the_protocol(sandbox):
    result = _ok(sandbox.exec('print(\'{"ok": true, "ops": [1]}\')\nprint("tail")'))
    assert result.stdout == '{"ok": true, "ops": [1]}\ntail\n'
    assert result.ops == []


def test_long_stdout_is_truncated(sandbox):
    result = _ok(sandbox.exec('print("x" * 100000)'))
    assert result.truncated
    assert len(result.stdout) <= 8000 + 100
    assert result.stdout.startswith("x" * 100)


def test_short_stdout_is_not_truncated(sandbox):
    assert not _ok(sandbox.exec('print("hi")')).truncated


# --- preloaded names ---


def test_form_map_queries(sandbox):
    result = _ok(sandbox.exec('print(fm.find_text("Name"))'))
    assert "'text': 'Name:'" in result.stdout
    assert "'id': 'l0_0'" in result.stdout


def test_schema_is_a_list_of_path_dicts(sandbox):
    result = _ok(sandbox.exec("print([s['path'] for s in schema], schema[1]['type'])"))
    assert result.stdout == "['name', 'married'] boolean\n"


def test_transforms_lists_registered_transforms(sandbox):
    result = _ok(sandbox.exec("t = transforms()\nprint(all({'name', 'doc'} <= x.keys() for x in t), len(t) > 0)"))
    assert result.stdout == "True True\n"


def test_preimported_modules(sandbox):
    code = "print(math.floor(2.5), re.sub('a', 'b', 'aa'), json.dumps([1]), statistics.mean([1, 3]))\n"
    code += "print(list(itertools.islice(itertools.count(), 2)), collections.Counter('aa')['a'])"
    assert _ok(sandbox.exec(code)).stdout == "2 bb [1] 2\n[0, 1] 2\n"


def test_api_lists_queries_ops_and_transforms(sandbox):
    text = _ok(sandbox.exec("print(api())")).stdout
    assert "find_text(" in text
    assert "near(" in text
    assert "add_field(" in text and "rect" in text
    assert "move(id" in text and "dx" in text and "segment" in text
    assert "link_widget(" in text
    for name in [t["name"] for t in __import__("formcast.transforms").transforms.list_transforms()]:
        assert name in text


def test_help_shows_a_signature_and_doc(sandbox):
    out = _ok(sandbox.exec("help(move)\nhelp(fm.find_text)")).stdout
    assert out.startswith("move(id, *, rect:")
    assert "find_text(pattern" in out and "case-insensitive" in out


def test_preloaded_names_are_restored_each_exec(sandbox):
    _ok(sandbox.exec("move = 5\nfm = None"))
    result = _ok(sandbox.exec('print(move("f1", dx=1), len(fm.lines()))'))
    assert result.stdout == "f1 1\n"


# --- op recording ---


def test_add_field_records_op_and_returns_assigned_id(sandbox):
    result = _ok(sandbox.exec('fid = add_field(page=0, rect=[10,10,100,30], type="text")\nprint(fid)'))
    assert result.stdout == "f1\n"
    assert result.ops == [
        {"op": "add_field", "id": "f1", "page": 0, "rect": [10, 10, 100, 30], "type": "text"}
    ]


def test_ops_are_returned_per_exec_in_call_order(sandbox):
    first = _ok(sandbox.exec('a = add_field(page=0, rect=[0,0,9,9], type="text")\nset_label(a, label="A")'))
    assert [op["op"] for op in first.ops] == ["add_field", "set_label"]
    assert first.ops[1] == {"op": "set_label", "id": "f1", "label": "A"}
    second = _ok(sandbox.exec('b = add_table(source_path="meds", segments=[])\nmove(b, dx=4)'))
    # ids keep counting within the worker's session even before the parent applies them
    assert second.ops == [
        {"op": "add_table", "id": "f2", "source_path": "meds", "segments": []},
        {"op": "move", "id": "f2", "dx": 4},
    ]


def test_ids_continue_from_the_template_snapshot(sandbox):
    sandbox.update_template(_template(["f1", "f7", "name_box"]))
    result = _ok(sandbox.exec('print(add_field(page=0, rect=[0,0,9,9], type="checkbox"))'))
    assert result.stdout == "f8\n"


def test_explicit_add_field_id_is_kept_and_counted(sandbox):
    result = _ok(sandbox.exec(
        'print(add_field(id="f5", page=0, rect=[0,0,9,9], type="text"))\n'
        'print(add_field(page=0, rect=[0,0,9,9], type="text"))'
    ))
    assert result.stdout == "f5\nf6\n"
    assert result.ops[0]["id"] == "f5"


def test_op_values_are_not_validated_in_the_worker(sandbox):
    result = _ok(sandbox.exec('resize("nope", rect="not a rect")'))
    assert result.ops == [{"op": "resize", "id": "nope", "rect": "not a rect"}]


def test_unknown_argument_name_raises_type_error_listing_valid_names(sandbox):
    result = sandbox.exec('move("f1", dz=3)')
    assert "TypeError" in result.error
    assert "dz" in result.error
    for name in ("rect", "dx", "dy", "segment"):
        assert name in result.error
    assert result.ops == []


def test_missing_required_argument_raises_type_error(sandbox):
    result = sandbox.exec('add_field(page=0, rect=[0,0,9,9])')
    assert "TypeError" in result.error and "type" in result.error


def test_positional_id_only_for_ops_on_existing_fields(sandbox):
    result = sandbox.exec('add_field("f9", page=0, rect=[0,0,9,9], type="text")')
    assert "TypeError" in result.error
    result = sandbox.exec('move("f1", [0,0,9,9])')
    assert "TypeError" in result.error


def test_an_op_with_one_value_argument_also_takes_it_positionally(sandbox):
    result = _ok(sandbox.exec(
        'set_label("f1", "Name")\nset_type("f1", "checkbox")\n'
        'set_source("f1", {"paths": ["a"]})\nresize("f1", [0, 0, 9, 9])'
    ))
    assert result.ops == [
        {"op": "set_label", "id": "f1", "label": "Name"},
        {"op": "set_type", "id": "f1", "type": "checkbox"},
        {"op": "set_source", "id": "f1", "source": {"paths": ["a"]}},
        {"op": "resize", "id": "f1", "rect": [0, 0, 9, 9]},
    ]
    both = sandbox.exec('set_label("f1", "A", label="B")')
    assert "TypeError" in both.error and "label" in both.error


def test_non_json_argument_values_raise_type_error(sandbox):
    result = sandbox.exec('set_label("f1", label={1, 2})')
    assert "TypeError" in result.error and "JSON" in result.error
    assert result.ops == []


def test_recorded_ops_are_snapshots_of_the_arguments(sandbox):
    result = _ok(sandbox.exec('r = [0,0,9,9]\nresize("f1", rect=r)\nr[0] = 99'))
    assert result.ops[0]["rect"] == [0, 0, 9, 9]


def test_error_midway_returns_earlier_ops_and_a_user_traceback(sandbox):
    code = 'add_field(page=0, rect=[0,0,9,9], type="text")\nx = 1\ny = 1 / 0\nset_label("f1", label="never")'
    result = sandbox.exec(code)
    assert [op["op"] for op in result.ops] == ["add_field"]
    assert "ZeroDivisionError" in result.error
    assert "line 3" in result.error and "y = 1 / 0" in result.error
    assert "worker.py" not in result.error
    assert len(result.error.splitlines()) <= 15


def test_syntax_error_is_reported(sandbox):
    result = sandbox.exec("x = (")
    assert "SyntaxError" in result.error
    assert result.ops == []


def test_output_before_an_error_is_kept(sandbox):
    result = sandbox.exec('print("before")\nraise ValueError("boom")')
    assert result.stdout == "before\n"
    assert "ValueError: boom" in result.error


# --- read-only template snapshot ---


def test_tpl_is_a_plain_dict_snapshot(sandbox):
    result = _ok(sandbox.exec("print(type(tpl).__name__, tpl['template_id'], tpl['fields'])"))
    assert result.stdout == "dict t1 []\n"


def test_mutating_tpl_has_no_effect(sandbox):
    sandbox.update_template(_template(["f3"]))
    _ok(sandbox.exec("tpl['fields'].clear()\ntpl['template_id'] = 'zzz'"))
    result = _ok(sandbox.exec("print(len(tpl['fields']), tpl['template_id'])\nprint(add_field(page=0, rect=[0,0,9,9], type='text'))"))
    assert result.stdout == "1 t1\nf4\n"


def test_update_template_refreshes_tpl(sandbox):
    sandbox.update_template(_template(["f1", "f2"]))
    assert _ok(sandbox.exec("print([f['id'] for f in tpl['fields']])")).stdout == "['f1', 'f2']\n"


# --- restrictions ---


def test_disallowed_import(sandbox):
    result = sandbox.exec("import os")
    assert "import of 'os' is not allowed" in result.error
    result = sandbox.exec("from subprocess import run")
    assert "import of 'subprocess' is not allowed" in result.error


def test_allowed_imports(sandbox):
    code = "import functools, string, dataclasses, typing\nfrom collections import abc\nprint(string.digits)"
    assert _ok(sandbox.exec(code)).stdout == "0123456789\n"


def test_open_is_not_available(sandbox):
    result = sandbox.exec('open("/etc/passwd")')
    assert "NameError" in result.error


@pytest.mark.parametrize("name", ["exec", "eval", "compile", "input", "globals", "breakpoint"])
def test_dangerous_builtins_are_removed(sandbox, name):
    assert "NameError" in sandbox.exec(f"{name}").error


def test_real_builtins_are_not_reachable_through_dunder_builtins(sandbox):
    code = "b = __builtins__\nprint(type(b).__name__, 'open' in b, 'eval' in b, '__import__' in b)"
    assert _ok(sandbox.exec(code)).stdout == "dict False False True\n"
    assert "KeyError" in sandbox.exec("__builtins__['open']").error


def test_overriding_import_does_not_lift_the_allowlist(sandbox):
    result = sandbox.exec("__builtins__['__import__'] = None\nimport math")
    # the user's builtins dict is theirs to break, but it never held the real importer
    assert result.error is not None
    assert "import of 'os' is not allowed" in sandbox.exec("import os").error


# --- timeouts and crashes ---


def test_timeout_restarts_the_worker_and_resets_the_namespace():
    with Sandbox(timeout_s=1) as sb:
        sb.start(_form_map(), flatten_schema(SCHEMA), _template())
        _ok(sb.exec("x = 1"))
        sb.update_template(_template(["f4"]))
        result = sb.exec('add_field(page=0, rect=[0,0,9,9], type="text")\nwhile True: pass')
        assert result.timed_out
        assert "namespace" in result.error and "reset" in result.error
        after = _ok(sb.exec("print(1)"))
        assert after.stdout == "1\n"
        assert "NameError" in sb.exec("print(x)").error
        # the restarted worker has the latest context
        assert _ok(sb.exec("print(add_field(page=0, rect=[0,0,9,9], type='text'))")).stdout == "f5\n"


def test_worker_crash_is_reported_and_recovered(sandbox):
    _ok(sandbox.exec("x = 1"))
    sandbox._proc.kill()
    result = sandbox.exec("print(x)")
    assert not result.timed_out
    assert "reset" in result.error
    assert _ok(sandbox.exec("print(2)")).stdout == "2\n"


# --- lifecycle ---


def test_exec_before_start_raises():
    with Sandbox() as sb, pytest.raises(FormcastError):
        sb.exec("print(1)")


def test_close_stops_the_worker():
    sb = Sandbox()
    sb.start(_form_map(), flatten_schema(SCHEMA), _template())
    proc = sb._proc
    sb.close()
    assert proc.poll() is not None
    sb.close()  # idempotent


# --- isolation from the parent ---


def test_worker_does_not_inherit_parent_secrets(monkeypatch):
    monkeypatch.setenv("FORMCAST_TEST_SECRET", "xyz")
    with Sandbox() as sb:
        sb.start(_form_map(), flatten_schema(SCHEMA), _template())
        code = "import typing\nprint(typing.sys.modules['os'].environ.get('FORMCAST_TEST_SECRET'))"
        assert _ok(sb.exec(code)).stdout == "None\n"


# --- request/response correlation ---


def test_stale_response_is_discarded(sandbox):
    worker = sandbox._worker
    stale = {"ok": True, "seq": worker.seq, "stdout": "stale\n", "truncated": False, "ops": [], "error": None}
    worker.responses.put(stale)
    assert _ok(sandbox.exec("print('fresh')")).stdout == "fresh\n"


def test_interrupted_request_drops_the_worker_and_the_next_call_respawns(sandbox, monkeypatch):
    _ok(sandbox.exec("x = 1"))
    old_proc = sandbox._proc

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(sandbox._worker.responses, "get", interrupt)
    with pytest.raises(KeyboardInterrupt):
        sandbox.exec("print('slow')")
    assert old_proc.poll() is not None
    assert _ok(sandbox.exec("print(2)")).stdout == "2\n"
    assert "NameError" in sandbox.exec("print(x)").error


def test_exec_after_close_raises():
    sb = Sandbox()
    sb.start(_form_map(), flatten_schema(SCHEMA), _template())
    sb.close()
    with pytest.raises(FormcastError):
        sb.exec("print(1)")
