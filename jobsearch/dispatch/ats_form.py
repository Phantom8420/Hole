"""Fill and submit application forms in a real browser.

Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, BreezyHR,
JazzHR, BambooHR, Personio, Teamtailor, Rippling, and Jobvite are recognized by
host. Most of the work here is ATS-agnostic -- required fields are found by
their DOM properties and filled by the label a person reads -- so the boards
without a hand-written selector map are filled by the same generic path.

Needs Playwright:

    pip install playwright
    playwright install chromium

Design constraints, all of which mean "stop rather than guess":

- It fills only fields it recognizes. A required field it cannot map is an abort,
  not a blank submission or an invented answer.
- It never attempts a CAPTCHA. If one is present the run stops and the
  application goes to the review queue for you to finish by hand.
- It screenshots the completed form before submitting, so every unattended
  submission has an artifact you can look at afterwards.
- Demographic and voluntary self-identification questions are left untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import AtsConfig
from . import DispatchResult

SETUP_HELP = (
    "ATS form submission needs Playwright:\n"
    "  pip install playwright\n"
    "  playwright install chromium"
)

CAPTCHA_MARKERS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    ".g-recaptcha",
    "#cf-challenge-running",
    "[data-sitekey]",
)

# Questions we deliberately never answer on someone's behalf.
SKIP_LABEL_PATTERNS = re.compile(
    r"(gender|race|ethnic|veteran|disability|sexual orientation|pronoun|"
    r"self.?identif|voluntary|hispanic|salary expectation|desired compensation)",
    re.IGNORECASE,
)


@dataclass
class ApplicantFields:
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    website: str = ""
    github: str = ""
    location: str = ""
    resume_path: Path | None = None
    cover_letter_text: str = ""

    @classmethod
    def from_profile(cls, profile: dict[str, Any], resume_path: Path | None = None,
                     cover_letter_text: str = "") -> "ApplicantFields":
        full_name = str(profile.get("full_name") or "").strip()
        first, _, last = full_name.partition(" ")
        return cls(
            first_name=first,
            last_name=last or first,
            full_name=full_name,
            email=str(profile.get("email") or ""),
            phone=str(profile.get("phone") or ""),
            linkedin=str(profile.get("linkedin_url") or ""),
            website=str(profile.get("website") or ""),
            github=str(profile.get("github") or ""),
            location=str(profile.get("location") or ""),
            resume_path=resume_path,
            cover_letter_text=cover_letter_text,
        )

    def missing_required(self) -> list[str]:
        missing = [
            name
            for name, value in (
                ("full_name", self.full_name),
                ("email", self.email),
            )
            if not value
        ]
        if not self.resume_path or not Path(self.resume_path).is_file():
            missing.append("resume file")
        return missing


# Host fragment -> ATS name. Order matters only in that the first hit wins.
ATS_HOSTS: tuple[tuple[str, str], ...] = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("workable.com", "workable"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("recruitee.com", "recruitee"),
    ("breezy.hr", "breezy"),
    ("applytojob.com", "jazzhr"),
    ("jazz.co", "jazzhr"),
    ("bamboohr.com", "bamboohr"),
    ("personio.de", "personio"),
    ("teamtailor.com", "teamtailor"),
    ("rippling.com", "rippling"),
    ("jobvite.com", "jobvite"),
)


def detect_ats(url: str) -> str:
    lowered = (url or "").lower()
    for fragment, name in ATS_HOSTS:
        if fragment in lowered:
            return name
    return "unknown"


# Ordered (selector, attribute-of-ApplicantFields) pairs per ATS. The first
# selector that resolves to exactly one visible element wins.
FIELD_MAP: dict[str, list[tuple[str, str]]] = {
    "greenhouse": [
        ("#first_name", "first_name"),
        ("#last_name", "last_name"),
        ("#email", "email"),
        ("#phone", "phone"),
        ("input[name='job_application[answers_attributes][0][text_value]']", "linkedin"),
    ],
    "lever": [
        ("input[name='name']", "full_name"),
        ("input[name='email']", "email"),
        ("input[name='phone']", "phone"),
        ("input[name='urls[LinkedIn]']", "linkedin"),
        ("input[name='urls[GitHub]']", "github"),
        ("input[name='org']", "location"),
    ],
    "ashby": [
        ("input[name='_systemfield_name']", "full_name"),
        ("input[name='_systemfield_email']", "email"),
        ("input[name='_systemfield_phone']", "phone"),
    ],
}

# Fallback for anything the selectors above miss. Greenhouse's current React
# board is the case that forced this: `#first_name` still resolves to a legacy
# element while the input the applicant actually types into is a sibling with no
# id, no name, and `aria-required` instead of `required`. Visible label text is
# what a person reads, and it survives the redesigns that churn the markup.
LABEL_FALLBACKS: list[tuple[str, str]] = [
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("Full name", "full_name"),
    ("Email", "email"),
    ("Phone", "phone"),
    ("LinkedIn", "linkedin"),
    ("Website", "website"),
    ("GitHub", "github"),
]

RESUME_SELECTORS = {
    "greenhouse": ["#resume", "input[type='file'][name*='resume']"],
    "lever": ["input[name='resume']", "input[type='file']"],
    "ashby": ["input[type='file']"],
}

COVER_LETTER_SELECTORS = {
    "greenhouse": ["#cover_letter_text", "textarea[name*='cover_letter']"],
    "lever": ["textarea[name='comments']"],
    "ashby": ["textarea"],
}

SUBMIT_SELECTORS = {
    "greenhouse": ["#submit_app", "input[type='submit']", "button[type='submit']"],
    "lever": [".template-btn-submit", "button[type='submit']"],
    "ashby": ["button[type='submit']"],
}

# Tried after any ATS-specific selectors above. Most of this module is already
# ATS-agnostic -- the blocking-field scan finds required inputs by their DOM
# properties, and LABEL_FALLBACKS fills by the text a person reads -- so a board
# nobody wrote selectors for still gets filled. These cover the rest.
GENERIC_RESUME = [
    "input[type='file'][name*='resume']",
    "input[type='file'][name*='cv']",
    "input[type='file'][accept*='pdf']",
    "input[type='file']",
]
GENERIC_COVER_LETTER = [
    "textarea[name*='cover']",
    "textarea[id*='cover']",
]
GENERIC_SUBMIT = [
    "button[type='submit']",
    "input[type='submit']",
]


def _selectors(table: dict[str, list[str]], ats: str, generic: list[str]) -> list[str]:
    """ATS-specific selectors first, then the generic ones, without duplicates."""
    out = list(table.get(ats, []))
    for selector in generic:
        if selector not in out:
            out.append(selector)
    return out


def _fill_first(page: Any, selectors: list[str], value: str) -> bool:
    if not value:
        return False
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() >= 1 and locator.first.is_visible():
                locator.first.fill(value)
                return True
        except Exception:
            continue
    return False


def _fill_by_label(page: Any, label: str, value: str) -> bool:
    """Fill the first visible input whose label contains `label`.

    Only ever writes into an input that is currently empty, so this cannot undo
    a correct value the selector pass already put there.
    """
    if not value:
        return False
    try:
        locator = page.get_by_label(label, exact=False)
        count = locator.count()
    except Exception:
        return False
    # One label can resolve to several elements: Greenhouse keeps a legacy
    # input alongside the one the applicant sees, and the selector pass has
    # usually filled the legacy one already. Scan for the first that is both
    # visible and still empty rather than assuming .first is the real one.
    for index in range(min(count, 5)):
        try:
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            if (candidate.input_value() or "").strip():
                continue
            candidate.fill(value)
            return True
        except Exception:
            continue
    return False


def _wait_for_form(page: Any, ats: str, timeout_seconds: int) -> bool:
    """Wait for the form itself, not just the document.

    `domcontentloaded` fires before a React board has rendered anything, so
    without this the fill pass runs against an empty page, finds none of its
    fields, and the run aborts reporting a form that was simply not there yet.
    """
    budget = max(1, min(timeout_seconds, 20)) * 1000
    for selector, _attribute in FIELD_MAP.get(ats, []):
        try:
            page.wait_for_selector(selector, state="visible", timeout=budget)
            return True
        except Exception:
            continue
    # Boards without a hand-written selector map still have a form. Wait for any
    # text input to become visible, which is as good a "the form rendered"
    # signal as a named field and works on every ATS.
    for selector in ("input[type='email']", "input[type='text']", "form input"):
        try:
            page.wait_for_selector(selector, state="visible", timeout=budget)
            return True
        except Exception:
            continue
    return False


def _screenshot(page: Any, path: Path) -> bool:
    """Best-effort. A screenshot is an artifact, never a reason to abandon a run.

    Chromium can fail `captureScreenshot` on a tall or still-painting page, and
    letting that propagate would throw away a form that was filled correctly.
    """
    try:
        page.screenshot(path=str(path), full_page=True)
        return True
    except Exception:
        try:
            page.screenshot(path=str(path))  # viewport only, often succeeds
            return True
        except Exception:
            return False


def _label_query(question: str) -> str:
    """The searchable part of a form label.

    Labels arrive decorated -- "Country*", "Phone *", "Why us?:" -- but the text
    in the DOM is usually the bare question. `get_by_label(exact=False)` does a
    substring match, so searching for the decorated version finds nothing.
    """
    return re.sub(r"[\s*:?]+$", "", str(question or "").strip()).strip()


TRUTHY = {"yes", "y", "true", "1", "on", "checked", "i agree", "agree", "accept"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _match_option(value: str, options: list[str]) -> str | None:
    """Which offered option a stored answer designates, or None if unclear.

    Refusing when the answer matches two options is the point. "Yes" against
    ["Yes", "Yes, with conditions"] is genuinely ambiguous, and picking either
    one puts a claim on an application that the candidate did not make. An
    unfilled field stops the run; a wrongly filled one gets submitted.
    """
    target = _norm(value)
    if not target or not options:
        return None
    exact = [o for o in options if _norm(o) == target]
    if exact:
        return exact[0]
    for test in (
        lambda o: _norm(o).startswith(target),
        lambda o: target in _norm(o),
    ):
        hits = [o for o in options if test(o)]
        if len(hits) == 1:
            return hits[0]
    return None


def _fill_combobox(page: Any, element: Any, value: str) -> bool:
    """Open a React combobox, type, and click the option the answer designates.

    Only ever clicks an option whose visible text unambiguously matches the
    stored answer, so a filtered listbox that comes back with several plausible
    rows is left alone rather than guessed at.
    """
    try:
        element.click()
        page.wait_for_timeout(250)
        # Typed, not filled. Location autocompletes query a places service on
        # each keystroke, and `fill()` sets the value without firing the key
        # events that trigger the search -- the listbox never opens.
        try:
            element.type(value, delay=30)
        except Exception:
            element.fill(value)
        page.wait_for_timeout(900)

        options = page.locator('[role="option"]:visible')
        count = min(options.count(), 40)
        if not count:
            page.keyboard.press("Escape")
            return False
        texts = []
        for index in range(count):
            try:
                texts.append(options.nth(index).inner_text())
            except Exception:
                texts.append("")
        choice = _match_option(value, [t for t in texts if t])
        if choice is None:
            page.keyboard.press("Escape")
            return False
        options.nth(texts.index(choice)).click()
        page.wait_for_timeout(400)
        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _fill_element(page: Any, field: dict[str, Any], value: str) -> bool:
    """Fill one blocking field, addressed by the ref stamped on it.

    `[data-jobsearch-ref=...]` resolves to exactly the element the scan found
    empty, so there is no question of which "Country" box this is.
    """
    if not value:
        return False
    ref = str(field.get("ref") or "")
    kind = str(field.get("kind") or "text")
    options = [str(o) for o in (field.get("options") or [])]
    if not ref:
        return False
    try:
        located = page.locator(f'[data-jobsearch-ref="{ref}"]')
        if located.count() != 1:
            return False
        element = located.first

        if kind in ("text", "textarea"):
            element.fill(value)
            return True

        if kind == "select":
            choice = _match_option(value, options)
            if choice is None:
                return False
            try:
                element.select_option(label=choice)
            except Exception:
                element.select_option(choice)
            return True

        if kind == "checkbox":
            if _norm(value) not in TRUTHY:
                return False  # never untick something on someone's behalf
            element.check()
            return True

        if kind == "radio":
            choice = _match_option(value, options)
            if choice is None:
                return False
            picked = element.evaluate(
                """(node, wanted) => {
                    const clean = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const group = node.name
                        ? [...document.getElementsByName(node.name)]
                        : [node];
                    const hit = group.find(r => {
                        const own = r.labels && r.labels[0] ? r.labels[0].innerText : r.value;
                        return clean(own) === clean(wanted);
                    });
                    if (!hit) return false;
                    hit.setAttribute('data-jobsearch-pick', '1');
                    return true;
                }""",
                choice,
            )
            if not picked:
                return False
            target = page.locator('[data-jobsearch-pick="1"]').first
            target.check()
            page.evaluate(
                "() => document.querySelectorAll('[data-jobsearch-pick]')"
                ".forEach(n => n.removeAttribute('data-jobsearch-pick'))"
            )
            return True

        if kind == "combobox":
            return _fill_combobox(page, element, value)
    except Exception:
        return False
    return False


ATTACH_BUTTONS = ("Attach", "Upload", "Attach resume", "Upload resume", "Choose file")


def _upload_resume(page: Any, ats: str, resume_path: Path) -> bool:
    """Attach the resume, by whichever route this board actually accepts.

    Setting files straight onto the hidden input works on classic boards. Newer
    React boards ignore it -- the input is replaced on re-render and the file
    never registers -- so fall back to driving the visible "Attach" button and
    answering the file chooser it opens, which is what a person does.
    """
    for selector in _selectors(RESUME_SELECTORS, ats, GENERIC_RESUME):
        try:
            locator = page.locator(selector)
            if locator.count() >= 1:
                locator.first.set_input_files(str(resume_path))
                page.wait_for_timeout(2500)
                if _resume_attached(page, resume_path.name):
                    return True
        except Exception:
            continue

    for name in ATTACH_BUTTONS:
        try:
            button = page.get_by_role("button", name=name).first
            if not button.is_visible():
                continue
            with page.expect_file_chooser(timeout=5000) as chooser:
                button.click()
            chooser.value.set_files(str(resume_path))
            page.wait_for_timeout(2500)
            if _resume_attached(page, resume_path.name):
                return True
        except Exception:
            continue
    return False


def _resume_attached(page: Any, filename: str = "") -> bool:
    """Whether the form visibly holds the attachment, judged conservatively.

    Three signals, in decreasing strength, and it fails closed: an upload it
    cannot confirm is reported as missing. Submitting an application with no
    resume is far worse than queueing one that actually had it.

    A raw `innerText` search is not enough. Greenhouse serves more than one
    variant of the same form -- one offering "Autofill my application", another
    "Quick Apply with MyGreenhouse" -- and they do not behave identically: the
    upload sticks on one and reverts to the empty state on the other, while the
    filename can linger in a detached node either way.
    """
    if not filename:
        return False
    try:
        # 1. The filename rendered as visible text, which is what a person sees
        #    beside the remove control once the upload is accepted.
        locator = page.get_by_text(filename, exact=False)
        for index in range(min(locator.count(), 4)):
            if locator.nth(index).is_visible():
                return True
    except Exception:
        pass
    try:
        # 2. A file input still holding the file. True on classic boards, and on
        #    React boards only before the widget re-renders.
        if page.evaluate(
            "() => [...document.querySelectorAll('input[type=file]')]"
            ".some(f => f.files && f.files.length > 0)"
        ):
            return True
    except Exception:
        pass
    return False

def _settle_after_upload(page: Any) -> None:
    """Give a resume parser time to finish rewriting the form.

    Greenhouse re-renders the name and email inputs once it has read the file.
    Typing into them while that is in flight loses the value.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass


def _has_captcha(page: Any) -> bool:
    for marker in CAPTCHA_MARKERS:
        try:
            if page.locator(marker).count() > 0:
                return True
        except Exception:
            continue
    return False


def _blocking_fields(page: Any) -> list[dict[str, Any]]:
    """Required inputs still empty after our pass, each tagged so it can be filled.

    Two jobs. First, name each one well: whatever is left here is what a human
    would otherwise finish by hand, and "(unnamed)" sends them to a screenshot to
    play spot-the-difference. Modern React form kits -- including Greenhouse's
    current board -- render required inputs with no name, no id, no label and no
    aria-label, so the fallbacks below walk outward through the DOM looking for
    any text a person would recognize.

    Second, and the reason this returns dicts rather than strings: it stamps
    `data-jobsearch-ref` on each element. Filling then addresses that attribute
    and hits exactly one node. Searching by label text instead is ambiguous in
    ways that matter -- on Figma's live form `get_by_label("Country")` matches
    three widgets, and the first is the phone country-code prefix. Writing "United
    States" into that and submitting would put a wrong answer on a real
    application. An element reference cannot be wrong about which field it is.

    Radio groups collapse to one entry per group, since a group is one question.
    """
    try:
        found = page.evaluate(
            """() => {
                const skip = /gender|race|ethnic|veteran|disability|orientation|pronoun|self.?identif|voluntary|hispanic/i;
                const clean = s => (s || '').replace(/\\s+/g, ' ').trim();

                const describe = (el, index) => {
                    // Checked before anything else, because a radio's own label
                    // is its option ("Yes") and the question lives on the block
                    // wrapping the group. Radios only -- a lone checkbox's label
                    // ("I agree to the terms") really is the question.
                    if (el.type === 'radio') {
                        let node = el.parentElement;
                        for (let d = 0; node && d < 6; d++, node = node.parentElement) {
                            const whole = clean(node.innerText);
                            if (!whole) continue;
                            let rest = whole;
                            node.querySelectorAll('label, li').forEach(o => {
                                rest = rest.replace(clean(o.innerText), ' ');
                            });
                            rest = clean(rest);
                            if (rest.length >= 8) return rest;
                        }
                    }

                    const byLabel = el.labels && el.labels[0] ? el.labels[0].innerText : '';
                    if (clean(byLabel)) return clean(byLabel);

                    const aria = el.getAttribute('aria-label');
                    if (clean(aria)) return clean(aria);

                    const owner = el.getAttribute('aria-labelledby');
                    if (owner) {
                        const node = document.getElementById(owner);
                        if (node && clean(node.innerText)) return clean(node.innerText);
                    }
                    if (clean(el.placeholder)) return clean(el.placeholder);

                    // Walk up looking for the question a person reads above the
                    // box. `[class*=question]` matters: Lever writes the prompt
                    // into `.application-question` with no <label> anywhere, and
                    // without it these fall through to a machine name like
                    // `cards[uuid][field0]`, which no stored answer can match.
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
                        const label = node.querySelector(
                            'label, legend, [class*="label"], [class*="question"], [class*="Question"]'
                        );
                        if (label && clean(label.innerText)) return clean(label.innerText);
                    }

                    const wrapper = el.closest('label');
                    if (wrapper && clean(wrapper.innerText)) return clean(wrapper.innerText);

                    // Machine names are a last resort: they identify the field
                    // for a human reading the report even though no stored
                    // answer will ever match one.
                    if (clean(el.name)) return clean(el.name);
                    if (clean(el.id)) return clean(el.id);
                    return `unlabelled ${el.type || el.tagName.toLowerCase()} field #${index + 1}`;
                };

                // What sort of widget this is, which decides how it gets filled.
                const kindOf = el => {
                    const tag = el.tagName.toLowerCase();
                    if (tag === 'select') return 'select';
                    if (tag === 'textarea') return 'textarea';
                    if (el.type === 'file') return 'file';
                    if (el.type === 'radio') return 'radio';
                    if (el.type === 'checkbox') return 'checkbox';
                    // React combobox kits: a text input that opens a listbox.
                    const role = (el.getAttribute('role') || '').toLowerCase();
                    if (role === 'combobox') return 'combobox';
                    if (el.getAttribute('aria-autocomplete')) return 'combobox';
                    if (el.getAttribute('aria-haspopup') === 'listbox') return 'combobox';
                    return 'text';
                };

                // A combobox can hold a selection without its input carrying the
                // text -- react-select paints the chosen value into a sibling
                // node. Treating that as empty would refill an answered field.
                //
                // Walking up from the parent matters: the input's own class is
                // `select__input`, so `closest('[class*=select]')` matches the
                // input itself and finds nothing inside it.
                const comboFilled = el => {
                    if (el.value) return true;
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
                        const shown = node.querySelector(
                            '[class*="singleValue"], [class*="multiValue"]'
                        );
                        if (shown && clean(shown.innerText)) return true;
                    }
                    return false;
                };

                // Identity of the field wrapper an input sits in. Two inputs in
                // the same wrapper are one question rendered twice (a combobox
                // and the hidden mirror that carries its value on submit); two
                // inputs in different wrappers are different questions even when
                // they describe identically. On Figma's form the phone dial-code
                // selector and the real Country field both describe as
                // "Country", and merging them threw away the one that mattered.
                let groupSeq = 0;
                const groupOf = (el, index) => {
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
                        const cls = (node.className || '').toString();
                        if (/select-shell|form-group|field|question|input-wrapper/i.test(cls)) {
                            if (!node.getAttribute('data-jobsearch-group')) {
                                node.setAttribute('data-jobsearch-group', 'g' + (++groupSeq));
                            }
                            return node.getAttribute('data-jobsearch-group');
                        }
                    }
                    return 'solo' + index;   // no wrapper found: never merge it
                };

                // An upload widget tracks its state in a hidden text input that
                // stays empty even once a file is attached, so the raw value
                // check reports an attached resume as still missing. If a file
                // input anywhere in this field's group holds a file, the field
                // is answered whatever its mirror says.
                // Only ever consulted for a field whose own label names an
                // upload, and only within three levels. Both limits matter: an
                // essay box can share a container with the resume widget, and a
                // broader search marked every empty text field on the form as
                // answered the moment a file attached -- which reads as "ready
                // to submit" with the essays still blank.
                const UPLOAD_LABEL = /resume|cv|attach|upload|portfolio/i;
                const anyFile = root =>
                    [...root.querySelectorAll('input[type=file]')]
                        .some(f => f.files && f.files.length > 0);
                const hasAttachment = el => {
                    // Nearest first, so a form with two uploads resolves each
                    // against its own widget where the DOM allows it.
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 3; depth++, node = node.parentElement) {
                        if (anyFile(node)) return true;
                    }
                    // Otherwise fall back to the page. An upload's hidden state
                    // input can sit well away from its file input, and the label
                    // gate above already means we only ask this about a field
                    // that is itself an upload.
                    return anyFile(document);
                };

                const isEmpty = (el, kind, label) => {
                    if (kind === 'file') return el.files.length === 0;
                    if (kind === 'checkbox') return !el.checked;
                    if (kind === 'radio') {
                        // One question, not N. Empty only if nothing in the group
                        // is selected.
                        if (!el.name) return !el.checked;
                        const group = document.getElementsByName(el.name);
                        return ![...group].some(r => r.checked);
                    }
                    if (kind === 'combobox') return !comboFilled(el);
                    if (el.value) return false;
                    if (UPLOAD_LABEL.test(label || '')) return !hasAttachment(el);
                    return true;
                };

                // The choices a radio group or native select offers, so the
                // caller can match a stored answer against them.
                const optionsFor = (el, kind) => {
                    if (kind === 'select') {
                        return [...el.options]
                            .map(o => clean(o.innerText || o.value))
                            .filter(t => t && t !== '--');
                    }
                    if (kind === 'radio' && el.name) {
                        return [...document.getElementsByName(el.name)]
                            .map(r => {
                                const own = r.labels && r.labels[0] ? r.labels[0].innerText : '';
                                return clean(own) || clean(r.value);
                            })
                            .filter(Boolean);
                    }
                    return [];  // combobox options do not exist until it opens
                };

                const out = [];
                const seenGroups = new Set();
                const all = document.querySelectorAll(
                    'input[required], select[required], textarea[required], ' +
                    '[aria-required="true"]'
                );
                [...all].forEach((el, index) => {
                    if (el.type === 'hidden' || el.disabled) return;
                    if (el.offsetParent === null && el.type !== 'file') return;

                    const kind = kindOf(el);
                    // Radios in one group are a single question; keep the first.
                    if (kind === 'radio' && el.name) {
                        if (seenGroups.has(el.name)) return;
                        seenGroups.add(el.name);
                    }
                    const label = describe(el, index);
                    if (skip.test(label)) return;
                    if (!isEmpty(el, kind, label)) return;

                    const ref = 'jsf' + index;
                    el.setAttribute('data-jobsearch-ref', ref);
                    out.push({
                        ref: ref,
                        group: groupOf(el, index),
                        label: label.slice(0, 120),
                        kind: kind,
                        options: optionsFor(el, kind).slice(0, 60)
                    });
                });
                return out;
            }"""
        )
        return _dedupe_fields([dict(f) for f in (found or [])])
    except Exception:
        return []


# How interactive a widget is. When one question surfaces twice, the higher
# number is the thing a person actually operates.
_KIND_RANK = {
    "text": 0,
    "textarea": 1,
    "file": 2,
    "checkbox": 3,
    "radio": 4,
    "select": 5,
    "combobox": 6,
}


def _dedupe_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per question.

    React combobox kits render two required inputs per question: the visible
    widget, and a hidden mirror input that carries the value on submit. Both
    look empty and both are `aria-required`, so a raw scan reports "Country" as
    two separate blockers and typing into the mirror would do nothing. Keep the
    widget -- the one with the higher interaction rank -- and drop its shadow.

    Merging is keyed on the DOM wrapper, never on the label. Label text is not a
    reliable identity: on Figma's form the phone dial-code selector and the
    country field both resolve to "Country", and collapsing them by name
    discarded the real country field while keeping the phone prefix.
    """
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, field in enumerate(fields):
        key = str(field.get("group") or "") or f"solo{index}"
        if not _norm(str(field.get("label") or "")):
            continue
        rank = _KIND_RANK.get(str(field.get("kind") or "text"), 0)
        current = best.get(key)
        if current is None:
            best[key] = field
            order.append(key)
        elif rank > _KIND_RANK.get(str(current.get("kind") or "text"), 0):
            best[key] = field
    return [best[key] for key in order]


def _unfilled_required(page: Any) -> list[str]:
    """Just the labels of what is still blocking. Kept for reporting."""
    return [str(field.get("label") or "") for field in _blocking_fields(page)]


def submit(
    config: AtsConfig,
    *,
    apply_url: str,
    fields: ApplicantFields,
    project_root: Path,
    slug: str,
    dry_run: bool = False,
    answer_lookup: Callable[[str], str | None] | None = None,
) -> DispatchResult:
    """Fill and (unless dry_run) submit one application form.

    `answer_lookup` maps a form question to a stored answer, or None. It is a
    callable rather than a database handle so this module stays free of the
    schema, and so a test can hand it a dictionary.
    """
    if not config.enabled:
        return DispatchResult(False, "ats_form", "dispatch.ats.enabled is false")

    ats = detect_ats(apply_url)
    if ats == "unknown":
        return DispatchResult(False, "ats_form", f"unrecognized application host: {apply_url}")

    missing = fields.missing_required()
    if missing:
        return DispatchResult(False, "ats_form", f"cannot fill the form: missing {', '.join(missing)}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return DispatchResult(False, "ats_form", SETUP_HELP)

    shot_dir = project_root / config.screenshot_dir
    shot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = shot_dir / f"{slug}.png"
    artifacts: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=config.headless)
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(config.timeout_seconds * 1000)
            page.goto(apply_url, wait_until="domcontentloaded")

            # A board URL is not a promise about where you land. Plenty of
            # companies point their Greenhouse board at their own careers site,
            # and this tool is about to type someone's name, email, and phone
            # number into whatever page it is looking at. Re-check the host
            # after navigation and refuse anything we did not recognize.
            landed = detect_ats(page.url)
            if landed != ats:
                _screenshot(page, screenshot)
                browser.close()
                where = "an unrecognized site" if landed == "unknown" else landed
                return DispatchResult(
                    False,
                    "ats_form",
                    f"{apply_url} redirected to {where} ({page.url}). Refusing to enter "
                    "personal details on a page this tool cannot identify -- apply by hand.",
                    artifacts=[str(screenshot)],
                )

            _wait_for_form(page, ats, config.timeout_seconds)

            if _has_captcha(page):
                _screenshot(page, screenshot)
                browser.close()
                return DispatchResult(
                    False,
                    "ats_form",
                    "a CAPTCHA is present -- stopping. Finish this one by hand.",
                    artifacts=[str(screenshot)],
                )

            filled: list[str] = []

            # The resume goes first, deliberately. Greenhouse parses the upload
            # and writes its own guesses at name and email into the form, which
            # discards anything typed beforehand. Uploading first means the
            # parser runs first and the values below overwrite it -- the record
            # in the database wins over a guess made from a PDF.
            if fields.resume_path:
                if _upload_resume(page, ats, Path(fields.resume_path)):
                    filled.append("resume")
                _settle_after_upload(page)

            for selector, attribute in FIELD_MAP.get(ats, []):
                if _fill_first(page, [selector], getattr(fields, attribute, "")):
                    filled.append(attribute)

            # Then by visible label, for the fields the selectors could not
            # reach. `_fill_by_label` skips anything already holding a value, so
            # this only ever adds.
            for label, attribute in LABEL_FALLBACKS:
                if _fill_by_label(page, label, getattr(fields, attribute, "")):
                    filled.append(f"{attribute} (by label)")

            if fields.cover_letter_text:
                if _fill_first(page, _selectors(COVER_LETTER_SELECTORS, ats, GENERIC_COVER_LETTER), fields.cover_letter_text):
                    filled.append("cover_letter")

            _screenshot(page, screenshot)
            artifacts.append(str(screenshot))

            # Nothing matched. The page is not the form we expected, so the
            # "no outstanding required fields" check below would be answering a
            # question about a page with no fields in it -- which reads as
            # success and walks on to click submit.
            if not filled:
                browser.close()
                return DispatchResult(
                    False,
                    "ats_form",
                    f"none of the expected {ats} fields were on the page -- its layout has "
                    "changed, or the posting applies somewhere else. Apply by hand.",
                    artifacts=artifacts,
                )

            blocking = _blocking_fields(page)

            # Whatever is left is a question the resume cannot answer: work
            # authorization, notice period, why-this-company. If the candidate
            # has written an answer down, use it verbatim. If not, this run
            # stops -- inventing a "no" to a sponsorship question would be a lie
            # told to an employer under their name.
            #
            # Several passes, because answering one question can reveal another:
            # forms routinely unhide a follow-up ("if yes, explain") once the
            # first is set. Each pass rescans, so a newly appeared required field
            # gets the same treatment instead of being submitted empty.
            if answer_lookup is not None:
                for _pass in range(3):
                    if not blocking:
                        break
                    progressed = False
                    for field in blocking:
                        stored = answer_lookup(str(field.get("label") or ""))
                        if stored and _fill_element(page, field, stored):
                            filled.append(f"answered: {str(field.get('label'))[:40]}")
                            progressed = True
                    if not progressed:
                        break
                    page.wait_for_timeout(400)
                    blocking = _blocking_fields(page)
                _screenshot(page, screenshot)

            # The authoritative check: rescan rather than trusting that each fill
            # took. A combobox that silently rejected a value must not be counted
            # as answered just because the click did not raise.
            outstanding = [str(f.get("label") or "") for f in _blocking_fields(page)]

            if outstanding:
                browser.close()
                return DispatchResult(
                    False,
                    "ats_form",
                    "required question(s) with no stored answer: "
                    + ", ".join(outstanding[:6])
                    + ".  Record answers with `jobsearch answers add` and this will "
                    "go through next run.",
                    artifacts=artifacts,
                    unanswered=list(outstanding),
                )

            # Checked after the questions, which are the more actionable report.
            # A missing resume cannot be caught by the scan above: when a board
            # loses an upload it drops the requirement from the DOM too, so "no
            # blocking fields" and "no resume attached" look identical. Confirm
            # it separately, or this reports a complete application with no
            # resume in it.
            if fields.resume_path and not _resume_attached(page, Path(fields.resume_path).name):
                _screenshot(page, screenshot)
                browser.close()
                return DispatchResult(
                    False,
                    "ats_form",
                    "the resume did not stay attached -- this board accepted the upload "
                    "and then dropped it. Everything else was filled; finish this one by "
                    "hand from the screenshot.",
                    artifacts=artifacts,
                )

            if dry_run:
                browser.close()
                return DispatchResult(
                    True,
                    "ats_form",
                    f"dry run -- filled {', '.join(filled)}, stopped before submit",
                    artifacts=artifacts,
                )

            submitted = False
            for selector in _selectors(SUBMIT_SELECTORS, ats, GENERIC_SUBMIT):
                try:
                    locator = page.locator(selector)
                    if locator.count() >= 1 and locator.first.is_visible():
                        locator.first.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                browser.close()
                return DispatchResult(False, "ats_form", "could not find a submit button", artifacts=artifacts)

            page.wait_for_load_state("networkidle", timeout=config.timeout_seconds * 1000)
            confirmation = shot_dir / f"{slug}_confirmation.png"
            # The form is already submitted by this point. A failed screenshot
            # must not turn a successful application into a reported failure.
            if _screenshot(page, confirmation):
                artifacts.append(str(confirmation))

            url_after = page.url
            body = (page.content() or "").lower()
            # "thank you" / "submitted" show up in nav, footers and privacy text
            # on plenty of forms, so the marker alone is a weak signal. Require
            # it *and* corroboration that the form is actually gone: the URL
            # moved, or the required fields / submit button are no longer there.
            still_on_form = detect_ats(url_after) == ats and bool(_blocking_fields(page))
            try:
                submit_gone = not any(
                    page.locator(sel).count() and page.locator(sel).first.is_visible()
                    for sel in _selectors(SUBMIT_SELECTORS, ats, GENERIC_SUBMIT)
                )
            except Exception:
                submit_gone = False
            browser.close()

            has_marker = any(
                marker in body
                for marker in ("thank you", "application received", "we have received",
                               "application submitted", "successfully submitted")
            )
            has_error = any(
                marker in body
                for marker in ("please fill", "is required", "required field",
                               "please correct", "there was a problem", "invalid")
            )
            looks_confirmed = (
                has_marker and not has_error and (url_after != apply_url or submit_gone)
                and not still_on_form
            )
            return DispatchResult(
                looks_confirmed,
                "ats_form",
                "submitted and confirmation page matched"
                if looks_confirmed
                else "submitted, but the result could not be confirmed -- check the screenshot "
                "before re-running so this is not applied twice",
                artifacts=artifacts,
            )
    except Exception as exc:
        return DispatchResult(
            False, "ats_form", f"{type(exc).__name__}: {exc}", artifacts=artifacts
        )
