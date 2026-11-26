"""Project-local model choices and a preview of the first-team questions."""

import json
from copy import copy
from pathlib import Path

PRESETS = ("current", "balanced", "strong", "manual")
ROLES = ("coordinator", "worker", "reviewer")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
TEXT = {
    "en": {
        "question": "How should this project use models?",
        "labels": (
            "Current model for everyone",
            "Strong coordinator and reviewer",
            "Strong model for everyone",
            "Choose by role",
        ),
        "descriptions": (
            "Use the model selected in the coordinator chat for new assignments.",
            "Use a strong model for coordination and review. Use a standard model for workers. A fast model needs qualification for each work type.",
            "Use a selected strong model for all roles.",
            "Choose models for the coordinator, workers and reviewer. A fast model is optional.",
        ),
        "pick": "Which available model should be used for {role}?",
        "unavailable": "{model} is unavailable for {role}. Which model should replace it?",
        "confirmation": "Use these models for this project?",
        "confirm": "Confirm selection",
        "change": "Change models",
        "input": "Enter a number or an exact ID (:cancel to stop): ",
        "invalid": "Choose a listed option.",
        "role": {
            "current": "the current chat",
            "strong": "strong work",
            "worker": "workers",
            "coordinator": "the coordinator",
            "reviewer": "the reviewer",
            "routine": "formal work",
        },
    },
    "ru": {
        "question": "Как распределить модели в этом проекте?",
        "labels": (
            "Текущая модель для всех",
            "Сильные координатор и ревьюер",
            "Сильная модель для всех",
            "Выбрать вручную",
        ),
        "descriptions": (
            "Новые поручения используют модель, выбранную в чате координатора.",
            "Координатор и ревьюер используют сильную модель. Исполнители работают на обычной. Быстрая модель получает формальные задачи после проверки качества.",
            "Одна выбранная сильная модель для всех ролей.",
            "Выбери модели для координатора, исполнителей и ревьюера. Быстрая модель для формальных задач по желанию.",
        ),
        "pick": "Какую доступную модель использовать для роли «{role}»?",
        "unavailable": "Модель {model} недоступна для роли «{role}». Какую выбрать взамен?",
        "confirmation": "Использовать эти модели для проекта?",
        "confirm": "Подтвердить выбор",
        "change": "Изменить модели",
        "input": "Введи номер или точный ID (:cancel — отмена): ",
        "invalid": "Выбери вариант из списка.",
        "role": {
            "current": "текущий чат",
            "strong": "сложные задачи",
            "worker": "исполнитель",
            "coordinator": "координатор",
            "reviewer": "ревьюер",
            "routine": "формальные задачи",
        },
    },
}


def check(ok, message):
    if not ok:
        raise ValueError(message)


def arguments(cmds, text):
    cmd = cmds.add_parser("model-choice", help="preview or save model choices for this project")
    cmd.add_argument("--preset", choices=PRESETS)
    cmd.add_argument("--lang", choices=TEXT, default="en")
    source = cmd.add_mutually_exclusive_group()
    source.add_argument("--codex", action="store_true", help="read the current Codex model catalog")
    source.add_argument("--catalog", type=Path, help="JSON client catalog for a different client")
    for role in ("current", "strong", *ROLES):
        cmd.add_argument("--" + role, type=text)
    routine = cmd.add_mutually_exclusive_group()
    routine.add_argument("--routine", type=text)
    routine.add_argument("--no-routine", action="store_true")
    cmd.add_argument("--current-effort", choices=EFFORTS)
    presentation = cmd.add_mutually_exclusive_group()
    presentation.add_argument("--apply", action="store_true", help="save the user's confirmed selection")
    presentation.add_argument("--interactive", action="store_true", help="ask and confirm in a terminal")


def catalog(root, a, codex_request):
    if a.catalog:
        rows = json.loads(a.catalog.read_text(encoding="utf-8-sig"))
    elif a.codex:
        rows, params = [], {"limit": 100, "includeHidden": False}
        cursors = set()
        while True:
            page = codex_request(root, "model/list", params)
            rows.extend(
                {
                    "id": row.get("model") or row.get("id"),
                    "label": row.get("displayName"),
                    "default": row.get("isDefault", False),
                    "effort": row.get("defaultReasoningEffort"),
                    "efforts": [
                        item.get("reasoningEffort") for item in row.get("supportedReasoningEfforts", [])
                    ],
                }
                for row in page["data"]
                if not row.get("hidden")
            )
            cursor = page.get("nextCursor")
            if not cursor:
                break
            check(cursor not in cursors, "model catalog returned a repeated cursor")
            cursors.add(cursor)
            params["cursor"] = cursor
    else:
        return None
    check(isinstance(rows, list) and rows, "the client did not provide a model catalog")
    models = {}
    for row in rows:
        check(
            isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip(),
            "each catalog entry requires a model id",
        )
        check(row["id"] not in models, "duplicate model id in catalog")
        models[row["id"]] = row
    return models


def read(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_policy'").fetchone():
        row = db.execute("SELECT configuration FROM model_policy WHERE id=1").fetchone()
        if row:
            return json.loads(row[0])


def write(db, policy):
    db.execute(
        "CREATE TABLE IF NOT EXISTS model_policy(id INTEGER PRIMARY KEY CHECK(id=1), configuration TEXT)"
    )
    db.execute("INSERT OR REPLACE INTO model_policy VALUES(1,?)", (json.dumps(policy),))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project'").fetchone():
        db.execute(
            "UPDATE project SET simple_model=?,strong_model=?",
            (policy["roles"]["worker"]["model"], policy["roles"]["reviewer"]["model"]),
        )


def effort(models, model, preferred, exact=None):
    data = models.get(model, {}) if models else {}
    supported = [value for value in (data.get("efforts") or []) if value in EFFORTS]
    if exact:
        check(not supported or exact in supported, f"{model} does not support effort {exact}")
        return exact
    return (
        next(v for v in (preferred, data.get("effort"), *supported) if v in supported)
        if supported
        else preferred
    )


def profiles(a, saved, models):
    """Choose model ids first. Resolve only choices that the catalog identifies unambiguously."""
    lang = TEXT[a.lang]
    preset = a.preset or (saved and saved["preset"])
    if not preset:
        check(not a.apply, "choose and confirm a preset before applying it")
        return {
            "status": "choose-preset",
            "question": lang["question"],
            "recommended": "balanced",
            "options": [
                {"id": key, "label": label, "description": detail}
                for key, label, detail in zip(PRESETS, lang["labels"], lang["descriptions"])
            ],
        }
    confirmed = saved if saved and saved["preset"] == preset else None
    questions = []

    def choose(role, value, *, category=None, optional=False, use_default=False):
        eligible = list(models or {})
        tagged = [
            name
            for name in eligible
            if (
                models[name].get("tier") == category
                if category
                else use_default and models[name].get("default") is True
            )
        ]
        if value is None:
            if len(tagged) == 1:
                return tagged[0]
            if optional:
                return None
            if len(eligible) == 1 and preset != "manual":
                return eligible[0]
        elif models is None or value in models:
            return value
        title = lang["role"][role]
        prompt = (
            lang["unavailable"].format(model=value, role=title) if value else lang["pick"].format(role=title)
        )
        questions.append({"role": role, "question": prompt, "options": eligible})
        return None

    previous = confirmed["roles"] if confirmed else {}
    before = {role: (previous[role]["model"] if previous else None) for role in ROLES}
    if preset == "current":
        current = choose("current", a.current or before["coordinator"])
        selected = dict.fromkeys(ROLES, current)
    elif preset in ("balanced", "strong"):
        strong = choose("strong", a.strong or before["reviewer"], category="strong")
        worker = (
            strong if preset == "strong" else choose("worker", a.worker or before["worker"], use_default=True)
        )
        selected = {"coordinator": strong, "worker": worker, "reviewer": strong}
    else:
        selected = {role: choose(role, getattr(a, role) or before[role]) for role in ROLES}
    routine = None
    if preset in ("balanced", "manual") and not a.no_routine:
        old = confirmed.get("routine") if confirmed else None
        if a.routine or old or (preset == "balanced" and not confirmed):
            routine = choose("routine", a.routine or (old and old["model"]), category="fast", optional=True)
        check(
            not a.routine or routine not in selected.values() or routine is None,
            "choose a distinct routine model",
        )
        if routine in selected.values():
            routine = None
    if questions:
        return {"status": "choose-models", "preset": preset, "questions": questions}
    default = {"coordinator": "high", "worker": "low", "reviewer": "medium"}
    roles = {}
    for role, model in selected.items():
        prior = previous.get(role)
        thinking = prior["thinking"] if prior and prior["model"] == model else None
        exact = a.current_effort if preset == "current" else None
        preferred = (
            ((models or {}).get(model, {}).get("effort") or "low") if preset == "current" else default[role]
        )
        roles[role] = {"model": model, "thinking": effort(models, model, thinking or preferred, exact)}
    if routine and (
        effort(models, routine, "low") != "low" or effort(models, selected["worker"], "low") != "low"
    ):
        raise ValueError("routine and worker models need low effort for paired qualification")
    policy = {
        "preset": preset,
        "roles": roles,
        "routine": {"model": routine, "thinking": "low"} if routine else None,
    }
    result = {"status": "ready", "policy": policy, "catalog_checked": models is not None}
    if policy != saved:
        result["confirmation"] = {
            "question": lang["confirmation"],
            "options": [{"id": key, "label": lang[key]} for key in ("confirm", "change")],
        }
    return result


def terminal(a, saved, models, source, output):
    """Present the same questionnaire as a host UI, keeping JSON on stdout."""
    lang = TEXT[a.lang]
    a = copy(a)

    def answer(question, choices):
        print("\n" + question, file=output)
        for number, (key, label) in enumerate(choices, 1):
            print(f"  {number}. {label}", file=output)
        while True:
            print(lang["input"], end="", file=output, flush=True)
            raw = source.readline()
            value = raw.strip()
            if not raw or value == ":cancel":
                return None
            for number, (key, label) in enumerate(choices, 1):
                if value in (str(number), key, label):
                    return key
            if value and not choices:
                return value
            print(lang["invalid"], file=output)

    try:
        while True:
            result = profiles(a, saved, models)
            if result["status"] == "choose-preset":
                a.preset = answer(result["question"], [(row["id"], row["label"]) for row in result["options"]])
                if a.preset is None:
                    break
            elif result["status"] == "choose-models":
                for question in result["questions"]:
                    choices = [(key, (models[key].get("label") or key)) for key in question["options"]]
                    choice = answer(question["question"], choices)
                    if choice is None:
                        return {"status": "cancelled"}
                    setattr(a, question["role"], choice)
            elif confirmation := result.get("confirmation"):
                policy = result["policy"]
                for role, profile in policy["roles"].items():
                    print(f"  {lang['role'][role]}: {profile['model']} ({profile['thinking']})", file=output)
                if profile := policy["routine"]:
                    print(
                        f"  {lang['role']['routine']}: {profile['model']} ({profile['thinking']})", file=output
                    )
                choice = answer(
                    confirmation["question"], [(row["id"], row["label"]) for row in confirmation["options"]]
                )
                if choice == "confirm":
                    return result
                if choice is None:
                    break
                saved = None
                for role in ("preset", "current", "current_effort", "strong", *ROLES, "routine"):
                    setattr(a, role, None)
            else:
                return result
    except KeyboardInterrupt:
        pass
    return {"status": "cancelled"}


def assignment(db, project, role):
    if policy := read(db):
        value = policy["roles"][role]
        return value["model"], value["thinking"]
    strong = role in ("coordinator", "reviewer")
    return project["strong_model" if strong else "simple_model"], "medium" if strong else "low"
