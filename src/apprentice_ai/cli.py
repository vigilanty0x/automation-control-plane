"""Machine-readable command-line interface for the local-first reference release."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .benchmark import run_benchmarks
from .errors import ApprenticeError, ValidationError
from .ingest import ingest_jsonl
from .learning import apply_answer, discover_routine, generate_question, segment_sessions
from .learnpack import export_learnpack, import_learnpack, inspect_learnpack, validate_learnpack
from .privacy import PrivacyGuard
from .service import capabilities, database_path, ensure_data_dir, run_reference_demo
from .skills import compile_skill, preview_stored_skill
from .store import EventStore
from .strictjson import canonical_bytes, load_file


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValidationError(message, code="CLI_USAGE")


def _parser() -> Parser:
    parser = Parser(prog="apprentice", description="Local-first, preview-only digital apprentice")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("APPRENTICE_DATA_DIR", ".apprentice"),
        help="local state directory (default: .apprentice)",
    )
    parser.add_argument("--json", action="store_true", help="reserved; output is always JSON")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)

    commands.add_parser("version")
    commands.add_parser("capabilities")
    init = commands.add_parser("init")
    init.add_argument("--name", default="Local apprentice")
    profiles = commands.add_parser("profiles")
    profiles.add_argument("operation", choices=("list",))
    demo = commands.add_parser("demo")
    demo.add_argument("--output")

    ingest = commands.add_parser("ingest")
    ingest.add_argument("profile_id")
    ingest.add_argument("path")
    ingest.add_argument("--goal")
    ingest.add_argument("--effect")
    ingest.add_argument("--split", choices=("induction", "holdout", "unknown"))
    ingest.add_argument("--climate")
    ingest.add_argument("--synthetic", action="store_true")

    timeline = commands.add_parser("timeline")
    timeline_sub = timeline.add_subparsers(dest="operation", required=True, parser_class=Parser)
    timeline_list = timeline_sub.add_parser("list")
    timeline_list.add_argument("profile_id")
    timeline_list.add_argument("--session")
    timeline_list.add_argument("--limit", type=int, default=1000)
    timeline_list.add_argument("--offset", type=int, default=0)
    timeline_verify = timeline_sub.add_parser("verify")
    timeline_verify.add_argument("profile_id")
    timeline_verify.add_argument("session_id")

    episodes = commands.add_parser("episodes")
    episodes_sub = episodes.add_subparsers(dest="operation", required=True, parser_class=Parser)
    for operation in ("build", "list"):
        item = episodes_sub.add_parser(operation)
        item.add_argument("profile_id")
    episode_show = episodes_sub.add_parser("show")
    episode_show.add_argument("profile_id")
    episode_show.add_argument("episode_id")

    routines = commands.add_parser("routines")
    routines_sub = routines.add_subparsers(dest="operation", required=True, parser_class=Parser)
    routine_discover = routines_sub.add_parser("discover")
    routine_discover.add_argument("profile_id")
    routine_discover.add_argument("--goal")
    routine_discover.add_argument("--effect")
    routine_list = routines_sub.add_parser("list")
    routine_list.add_argument("profile_id")
    routine_show = routines_sub.add_parser("show")
    routine_show.add_argument("profile_id")
    routine_show.add_argument("routine_id")

    questions = commands.add_parser("questions")
    questions_sub = questions.add_subparsers(dest="operation", required=True, parser_class=Parser)
    question_list = questions_sub.add_parser("list")
    question_list.add_argument("profile_id")
    question_generate = questions_sub.add_parser("generate")
    question_generate.add_argument("profile_id")
    question_generate.add_argument("routine_id")
    question_generate.add_argument("--daily-budget", type=int, default=3)
    question_answer = questions_sub.add_parser("answer")
    question_answer.add_argument("profile_id")
    question_answer.add_argument("question_id")
    question_answer.add_argument("choice", choices=("yes", "no", "unknown"))
    question_answer.add_argument("--explanation", default="")
    question_answer.add_argument("--synthetic", action="store_true")
    for operation in ("dismiss", "expire", "resume"):
        item = questions_sub.add_parser(operation)
        item.add_argument("profile_id")
        item.add_argument("question_id")
    question_snooze = questions_sub.add_parser("snooze")
    question_snooze.add_argument("profile_id")
    question_snooze.add_argument("question_id")
    question_snooze.add_argument("--until", required=True)

    memory = commands.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="operation", required=True, parser_class=Parser)
    memory_list = memory_sub.add_parser("list")
    memory_list.add_argument("profile_id")
    memory_explain = memory_sub.add_parser("explain")
    memory_explain.add_argument("profile_id")
    memory_explain.add_argument("memory_id")
    memory_invalidate = memory_sub.add_parser("invalidate-evidence")
    memory_invalidate.add_argument("profile_id")
    memory_invalidate.add_argument("evidence_ref")

    skill = commands.add_parser("skill")
    skill_sub = skill.add_subparsers(dest="operation", required=True, parser_class=Parser)
    skill_compile = skill_sub.add_parser("compile")
    skill_compile.add_argument("profile_id")
    skill_compile.add_argument("routine_id")
    skill_list = skill_sub.add_parser("list")
    skill_list.add_argument("profile_id")
    skill_list.add_argument("--include-stale", action="store_true")
    skill_preview = skill_sub.add_parser("preview")
    skill_preview.add_argument("profile_id")
    skill_preview.add_argument("skill_id")
    skill_preview.add_argument("version")
    skill_preview.add_argument("--inputs", help="strict JSON input file")

    pack = commands.add_parser("pack")
    pack_sub = pack.add_subparsers(dest="operation", required=True, parser_class=Parser)
    pack_export = pack_sub.add_parser("export")
    pack_export.add_argument("profile_id")
    pack_export.add_argument("skill_id")
    pack_export.add_argument("version")
    pack_export.add_argument("destination")
    for operation in ("validate", "inspect"):
        item = pack_sub.add_parser(operation)
        item.add_argument("path")
    pack_import = pack_sub.add_parser("import")
    pack_import.add_argument("profile_id")
    pack_import.add_argument("path")
    pack_list = pack_sub.add_parser("list")
    pack_list.add_argument("profile_id")
    pack_show = pack_sub.add_parser("show")
    pack_show.add_argument("profile_id")
    pack_show.add_argument("import_id")

    privacy = commands.add_parser("privacy")
    privacy_sub = privacy.add_subparsers(dest="operation", required=True, parser_class=Parser)
    privacy_audit = privacy_sub.add_parser("audit")
    privacy_audit.add_argument("--profile")
    privacy_scan = privacy_sub.add_parser("scan")
    privacy_scan.add_argument("path")
    privacy_purge = privacy_sub.add_parser("purge-profile")
    privacy_purge.add_argument("profile_id")
    privacy_purge.add_argument("--confirm", required=True)

    bench = commands.add_parser("bench")
    bench_sub = bench.add_subparsers(dest="operation", required=True, parser_class=Parser)
    bench_run = bench_sub.add_parser("run")
    bench_run.add_argument("profile_id")

    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--token")
    return parser


def _metadata(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {"synthetic": bool(args.synthetic)}
    for name in ("goal", "effect", "split", "climate"):
        value = getattr(args, name)
        if value is not None:
            result[name] = value
    return result


def _scan_file(path: str) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("privacy scan path must be a regular non-symlink file")
    if candidate.stat().st_size > 8 * 1024 * 1024:
        raise ValidationError("privacy scan file exceeds 8 MiB")
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"privacy scan requires readable UTF-8 text: {exc}") from exc
    _, findings = PrivacyGuard().scan_text(text)
    return {"path": str(candidate), "clean": not findings, "categories": list(findings)}


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "version":
        return {"name": "apprentice-ai", "version": __version__}
    if args.command == "capabilities":
        return capabilities()
    if args.command == "demo":
        return run_reference_demo(args.data_dir, output=args.output)
    if args.command == "pack" and args.operation == "validate":
        return validate_learnpack(args.path)
    if args.command == "pack" and args.operation == "inspect":
        return inspect_learnpack(args.path)
    if args.command == "privacy" and args.operation == "scan":
        return _scan_file(args.path)
    if args.command == "serve":
        from .api import serve

        serve(args.data_dir, port=args.port, token=args.token)
        return {"status": "stopped"}

    root = ensure_data_dir(args.data_dir)
    with EventStore(database_path(root)) as store:
        if args.command == "init":
            profile_id = store.create_profile(args.name)
            return {"status": "initialized", "data_dir": str(root), "profile_id": profile_id}
        if args.command == "profiles":
            return {"profiles": store.list_profiles()}
        if args.command == "ingest":
            return ingest_jsonl(store, args.profile_id, args.path, metadata=_metadata(args))
        if args.command == "timeline":
            if args.operation == "list":
                return {
                    "events": store.list_events(
                        args.profile_id,
                        session_id=args.session,
                        limit=args.limit,
                        offset=args.offset,
                    )
                }
            return store.verify_chain(args.profile_id, args.session_id)
        if args.command == "episodes":
            if args.operation == "build":
                return {"episodes": segment_sessions(store, args.profile_id)}
            if args.operation == "list":
                return {"episodes": store.list_episodes(args.profile_id)}
            return store.get_episode(args.profile_id, args.episode_id)
        if args.command == "routines":
            if args.operation == "discover":
                return discover_routine(store, args.profile_id, goal=args.goal, effect=args.effect)
            if args.operation == "list":
                return {"routines": store.list_routines(args.profile_id)}
            return store.get_routine(args.profile_id, args.routine_id)
        if args.command == "questions":
            if args.operation == "list":
                return {"questions": store.list_questions(args.profile_id)}
            if args.operation == "generate":
                return generate_question(
                    store, args.profile_id, args.routine_id, daily_budget=args.daily_budget
                )
            if args.operation == "answer":
                return apply_answer(
                    store,
                    args.profile_id,
                    args.question_id,
                    args.choice,
                    explanation=args.explanation,
                    synthetic=args.synthetic,
                )
            target = {"dismiss": "dismissed", "expire": "expired", "resume": "queued"}.get(
                args.operation, "snoozed"
            )
            return store.transition_question(
                args.profile_id,
                args.question_id,
                target,
                snoozed_until=getattr(args, "until", None),
            )
        if args.command == "memory":
            if args.operation == "list":
                return {"memories": store.list_memories(args.profile_id)}
            if args.operation == "explain":
                return store.get_memory(args.profile_id, args.memory_id)
            return {
                "invalidated": store.invalidate_by_evidence(args.profile_id, args.evidence_ref),
                "evidence_ref": args.evidence_ref,
            }
        if args.command == "skill":
            if args.operation == "compile":
                return compile_skill(store, args.profile_id, args.routine_id)
            if args.operation == "list":
                return {
                    "skills": store.list_skills(
                        args.profile_id, include_stale=args.include_stale
                    )
                }
            inputs = load_file(args.inputs) if args.inputs else {}
            if not isinstance(inputs, dict):
                raise ValidationError("skill preview inputs must be a JSON object")
            return preview_stored_skill(
                store, args.profile_id, args.skill_id, args.version, inputs
            )
        if args.command == "pack":
            if args.operation == "export":
                return export_learnpack(
                    store,
                    args.profile_id,
                    args.skill_id,
                    args.version,
                    args.destination,
                )
            if args.operation == "import":
                return import_learnpack(store, args.profile_id, args.path)
            if args.operation == "list":
                return {"imports": store.list_imports(args.profile_id)}
            return store.get_import(args.profile_id, args.import_id)
        if args.command == "privacy":
            if args.operation == "audit":
                return {"audit": store.audit_events(args.profile)}
            return store.purge_profile_data(args.profile_id, confirmation=args.confirm)
        if args.command == "bench":
            return run_benchmarks(store, args.profile_id)
    raise ValidationError("unsupported command", code="CLI_USAGE")


def _write(value: Any, stream: Any) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2))
    stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = _dispatch(args)
    except ApprenticeError as exc:
        _write({"error": {"code": exc.code, "message": exc.message}}, sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        _write({"error": {"code": "INTERRUPTED", "message": "operation interrupted"}}, sys.stderr)
        return 130
    except Exception:
        _write(
            {"error": {"code": "INTERNAL_ERROR", "message": "unexpected internal failure"}},
            sys.stderr,
        )
        return 70
    _write(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
