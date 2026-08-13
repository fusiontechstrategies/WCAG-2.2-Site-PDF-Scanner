#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WCAG 2.2 Site and PDF Scanner.

One-file accessibility assurance toolkit for websites, local HTML, and PDF
documents. Automated results are evidence for an accessibility review. They
are not, by themselves, a WCAG or PDF/UA conformance determination.
"""

import asyncio
import csv
import difflib
import hashlib
import html as html_lib
import importlib.metadata
import inspect
import io
import ipaddress
import json
import socket
import stat
import logging
import os
import platform
import re
import secrets
import shutil
# Subprocess execution is limited to this script through the current interpreter.
import subprocess  # nosec B404
import sys
import tempfile
import time
import warnings
from contextlib import asynccontextmanager
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple, Union
from urllib.parse import unquote, urldefrag, urljoin, urlparse
from urllib.request import url2pathname
from urllib.robotparser import RobotFileParser

import aiohttp
from bs4 import BeautifulSoup, Tag
import click
import cssutils
from cssutils.css import CSSStyleRule, Property
from defusedxml import ElementTree as SafeET
try:
    from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright, async_playwright
    PLAYWRIGHT_READY = True
except ImportError:
    Browser = Page = Playwright = Any

    class PlaywrightError(Exception):
        """Fallback exception when Playwright is unavailable."""

    async_playwright = None
    PLAYWRIGHT_READY = False
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table

warnings.filterwarnings("ignore", category=UserWarning, message="pkg_resources is deprecated as an API.")

# Ensure UTF-8 console output everywhere (rich emits ✓/✗ and box-drawing chars that crash on a
# Windows cp1252 console when output is redirected, piped, or run in CI/--ci-mode).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    # Console encoding is optional, so a best-effort failure is safe to ignore.
    except Exception:  # noqa: S112  # nosec B112
        continue


################################################################################
# BEGIN core.py


# ==============================================================================
# ENUMS AND CONSTANTS
# ==============================================================================

class WCAGLevel(Enum):
    A = "A"
    AA = "AA"
    AAA = "AAA"

    def __le__(self, other):
        if self.__class__ is other.__class__:
            order = list(WCAGLevel)
            return order.index(self) <= order.index(other)
        return NotImplemented

    def __lt__(self, other):
        if self.__class__ is other.__class__:
            order = list(WCAGLevel)
            return order.index(self) < order.index(other)
        return NotImplemented

    def __ge__(self, other):
        if self.__class__ is other.__class__:
            order = list(WCAGLevel)
            return order.index(self) >= order.index(other)
        return NotImplemented

    def __gt__(self, other):
        if self.__class__ is other.__class__:
            order = list(WCAGLevel)
            return order.index(self) > order.index(other)
        return NotImplemented


class IssueSeverity(Enum):
    CRITICAL = "Critical"
    SERIOUS = "Serious"
    MODERATE = "Moderate"
    MINOR = "Minor"
    INFO = "Informational"


class FixType(Enum):
    AUTOMATIC = "Automatic"
    SEMI_AUTOMATIC = "Semi-Automatic"
    MANUAL = "Manual"


class AnalysisMode(Enum):
    CRAWLER = "Crawler"
    STATIC = "Static"
    DYNAMIC = "Dynamic"
    AXE = "Axe-core"
    CONTENT = "Content-NLP"
    CSS = "CSS"
    CONSISTENCY = "Cross-Page"
    VALIDATION = "Validation"
    SPELLING = "Spelling"


# ARIA Constants (Expanded for more comprehensive checks)
VALID_ARIA_ROLES = {
    "alert", "alertdialog", "application", "article", "banner", "button", "cell", "checkbox", "columnheader",
    "combobox", "complementary", "contentinfo", "definition", "dialog", "directory", "document", "feed", "figure",
    "form", "grid", "gridcell", "group", "heading", "img", "link", "list", "listbox", "listitem", "log", "main",
    "marquee", "math", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "navigation", "none",
    "note", "option", "presentation", "progressbar", "radio", "radiogroup", "region", "row", "rowgroup",
    "rowheader", "scrollbar", "search", "searchbox", "separator", "slider", "spinbutton", "status", "switch",
    "tab", "table", "tablist", "tabpanel", "term", "textbox", "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem"
}

VALID_ARIA_PROPS = {
    "aria-activedescendant", "aria-atomic", "aria-autocomplete", "aria-busy", "aria-checked", "aria-colcount",
    "aria-colindex", "aria-colspan", "aria-controls", "aria-current", "aria-describedby", "aria-details",
    "aria-disabled", "aria-dropeffect", "aria-errormessage", "aria-expanded", "aria-flowto", "aria-grabbed",
    "aria-haspopup", "aria-hidden", "aria-invalid", "aria-keyshortcuts", "aria-label", "aria-labelledby",
    "aria-level", "aria-live", "aria-modal", "aria-multiline", "aria-multiselectable", "aria-orientation",
    "aria-owns", "aria-placeholder", "aria-posinset", "aria-pressed", "aria-readonly", "aria-relevant",
    "aria-required", "aria-roledescription", "aria-rowcount", "aria-rowindex", "aria-rowspan", "aria-selected",
    "aria-setsize", "aria-sort", "aria-valuemax", "aria-valuemin", "aria-valuenow", "aria-valuetext"
}

# Mapping of ARIA roles to required attributes
ARIA_REQUIRED_PROPS = {
    "checkbox": ["aria-checked"],
    "combobox": ["aria-controls", "aria-expanded"],
    "grid": ["aria-readonly"],
    "gridcell": [],
    "listbox": [],
    "menuitemcheckbox": ["aria-checked"],
    "menuitemradio": ["aria-checked"],
    "progressbar": ["aria-valuemin", "aria-valuemax", "aria-valuenow"],
    "radio": ["aria-checked"],
    "radiogroup": [],
    "slider": ["aria-valuemin", "aria-valuemax", "aria-valuenow"],
    "spinbutton": ["aria-valuemin", "aria-valuemax", "aria-valuenow"],
    "switch": ["aria-checked"],
    "tab": ["aria-selected"],
    "tablist": [],
    "tabpanel": ["aria-labelledby"],
    "textbox": [],
    "treeitem": ["aria-expanded"],
    "alertdialog": ["aria-modal", "aria-label", "aria-labelledby"]
}


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================

@dataclass
class AccessibilityIssue:
    criterion: str
    criterion_name: str
    level: WCAGLevel
    severity: IssueSeverity
    mode: AnalysisMode
    description: str
    impact: str
    element: Optional[str] = None
    element_html: Optional[str] = None
    context_html: Optional[str] = None
    selector: Optional[str] = None
    fix_type: FixType = FixType.MANUAL
    suggested_fix: Optional[str] = None
    file_path: Optional[str] = None
    url: Optional[str] = None
    line_number: Optional[int] = None
    col_number: Optional[int] = None
    screenshot_path: Optional[str] = None
    additional_info: Dict[str, Any] = field(default_factory=dict)
    fixed: bool = False
    fix_applied: Optional[str] = None
    issue_hash: str = field(init=False)

    def __post_init__(self):
        # Create a stable hash for duplicate detection and tracking fixes
        import hashlib
        unique_str = f"{self.criterion}{self.url or self.file_path}{self.selector or self.element_html}{self.description}"
        self.issue_hash = hashlib.sha256(unique_str.encode('utf-8')).hexdigest()[:16]


@dataclass
class PassedCheck:
    criterion: str
    criterion_name: str
    level: WCAGLevel
    mode: AnalysisMode
    description: str
    elements_checked: int = 0
    file_path: Optional[str] = None
    url: Optional[str] = None
    details: Optional[str] = None


@dataclass
class AccessibilityReport:
    target: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    wcag_level_tested: WCAGLevel = WCAGLevel.AA
    issues: List[AccessibilityIssue] = field(default_factory=list)
    passed_checks: List[PassedCheck] = field(default_factory=list)
    all_files_analyzed: Set[str] = field(default_factory=set)
    all_urls_crawled: Set[str] = field(default_factory=set)
    broken_links: DefaultDict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    fixes_applied: DefaultDict[str, List[Dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    analysis_duration: float = 0.0
    summary: Dict[str, Any] = field(default_factory=dict)
    screenshot_dir: Optional[Path] = None

    def reconcile_results(self):
        """Remove criterion-level pass records contradicted by findings.

        The legacy analyzers record successful narrow checks independently.
        A page can therefore have both a failure and a pass for the same WCAG
        criterion. Until every rule has its own atomic identifier, suppress the
        contradictory pass so reports never imply criterion conformance.
        """
        failed = {
            (issue.criterion, issue.url or issue.file_path or "")
            for issue in self.issues
            if not issue.fixed
        }
        self.passed_checks = [
            check for check in self.passed_checks
            if (check.criterion, check.url or check.file_path or "") not in failed
        ]

    def compile_summary(self):
        self.reconcile_results()
        # Ensures all sets are converted to lists for JSON serialization later down
        self.summary = {
            "target": self.target,
            "timestamp": self.timestamp,
            "wcag_level_tested": self.wcag_level_tested.value,
            "analysis_duration_seconds": round(self.analysis_duration, 2),
            "total_urls_crawled": len(self.all_urls_crawled),
            "total_files_analyzed": len(self.all_files_analyzed),
            "total_issues": len(self.issues),
            "total_passed_checks": len(self.passed_checks),
            "passed_check_scope": "Narrow automated checks with no detected failure; not criterion conformance",
            "total_broken_links": sum(len(pages) for pages in self.broken_links.values()),
            "issues_by_severity": {
                sev.value: len([i for i in self.issues if i.severity == sev]) for sev in IssueSeverity
            },
            "issues_by_level": {
                lvl.value: len([i for i in self.issues if i.level == lvl]) for lvl in WCAGLevel
            },
            "issues_fixed": len([i for i in self.issues if i.fixed]),
            "issues_by_mode": {
                mode.value: len([i for i in self.issues if i.mode == mode]) for mode in AnalysisMode
            },
            "broken_links_by_target": {k: list(v) for k, v in self.broken_links.items()} if self.broken_links else {},
        }
        # Clean up empty categories for cleaner summary
        self.summary["issues_by_severity"] = {k: v for k, v in self.summary["issues_by_severity"].items() if v > 0}
        self.summary["issues_by_level"] = {k: v for k, v in self.summary["issues_by_level"].items() if v > 0}
        self.summary["issues_by_mode"] = {k: v for k, v in self.summary["issues_by_mode"].items() if v > 0}


# ==============================================================================
# WCAG 2.2 CRITERIA DATABASE
# ==============================================================================

WCAG_CRITERIA_DATABASE: Dict[str, Dict[str, Any]] = {
    # Principle 1: Perceivable
    "1.1.1": {"name": "Non-text Content", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.CRITICAL},
    "1.2.1": {"name": "Audio-only and Video-only (Prerecorded)", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/audio-only-and-video-only-prerecorded.html"},
    "1.2.2": {"name": "Captions (Prerecorded)", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded.html"},
    "1.2.3": {"name": "Audio Description or Media Alternative (Prerecorded)", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/audio-description-or-media-alternative-prerecorded.html"},
    "1.2.4": {"name": "Captions (Live)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/captions-live.html"},
    "1.2.5": {"name": "Audio Description (Prerecorded)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/audio-description-prerecorded.html"},
    "1.3.1": {"name": "Info and Relationships", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "1.3.2": {"name": "Meaningful Sequence", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/meaningful-sequence.html"},
    "1.3.3": {"name": "Sensory Characteristics", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/sensory-characteristics.html"},
    "1.3.4": {"name": "Orientation", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/orientation.html"},
    "1.3.5": {"name": "Identify Input Purpose", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/identify-input-purpose.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.MODERATE},
    "1.4.1": {"name": "Use of Color", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html"},
    "1.4.2": {"name": "Audio Control", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/audio-control.html"},
    "1.4.3": {"name": "Contrast (Minimum)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.CRITICAL},
    "1.4.4": {"name": "Resize text", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/resize-text.html"},
    "1.4.5": {"name": "Images of Text", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/images-of-text.html"},
    "1.4.10": {"name": "Reflow", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/reflow.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "1.4.11": {"name": "Non-text Contrast", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.CRITICAL},
    "1.4.12": {"name": "Text Spacing", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/text-spacing.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "1.4.13": {"name": "Content on Hover or Focus", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},

    # Principle 2: Operable
    "2.1.1": {"name": "Keyboard", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/keyboard.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.CRITICAL},
    "2.1.2": {"name": "No Keyboard Trap", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/no-keyboard-trap.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.CRITICAL},
    "2.1.4": {"name": "Character Key Shortcuts", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/character-key-shortcuts.html"},
    "2.2.1": {"name": "Timing Adjustable", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/timing-adjustable.html"},
    "2.2.2": {"name": "Pause, Stop, Hide", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html"},
    "2.3.1": {"name": "Three Flashes or Below Threshold", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html"},
    "2.4.1": {"name": "Bypass Blocks", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/bypass-blocks.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.SERIOUS},
    "2.4.2": {"name": "Page Titled", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/page-titled.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.CRITICAL},
    "2.4.3": {"name": "Focus Order", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/focus-order.html"},
    "2.4.4": {"name": "Link Purpose (In Context)", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "2.4.5": {"name": "Multiple Ways", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/multiple-ways.html"},
    "2.4.6": {"name": "Headings and Labels", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/headings-and-labels.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.SERIOUS},
    "2.4.7": {"name": "Focus Visible", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "2.4.11": {"name": "Focus Not Obscured (Minimum)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "2.4.12": {"name": "Focus Not Obscured (Enhanced)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-enhanced.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "2.5.1": {"name": "Pointer Gestures", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/pointer-gestures.html"},
    "2.5.2": {"name": "Pointer Cancellation", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/pointer-cancellation.html"},
    "2.5.3": {"name": "Label in Name", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/label-in-name.html"},
    "2.5.4": {"name": "Motion Actuation", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/motion-actuation.html"},
    "2.5.7": {"name": "Dragging Movements", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html"},
    "2.5.8": {"name": "Target Size (Minimum)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},

    # Principle 3: Understandable
    "3.1.1": {"name": "Language of Page", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/language-of-page.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.CRITICAL},
    "3.1.2": {"name": "Language of Parts", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/language-of-parts.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "3.1.3": {"name": "Unusual Words", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/unusual-words.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MINOR},
    "3.1.4": {"name": "Abbreviations", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/abbreviations.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MINOR},
    "3.1.5": {"name": "Reading Level", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/reading-level.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "3.2.1": {"name": "On Focus", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/on-focus.html"},
    "3.2.2": {"name": "On Input", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/on-input.html"},
    "3.2.3": {"name": "Consistent Navigation", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/consistent-navigation.html"},
    "3.2.4": {"name": "Consistent Identification", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/consistent-identification.html"},
    "3.3.1": {"name": "Error Identification", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/error-identification.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "3.3.2": {"name": "Labels or Instructions", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions.html", "fix_type": FixType.SEMI_AUTOMATIC, "severity": IssueSeverity.CRITICAL},
    "3.3.3": {"name": "Error Suggestion", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/error-suggestion.html"},
    "3.3.4": {"name": "Error Prevention (Legal, Financial, Data)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/error-prevention-legal-financial-data.html"},
    "3.3.7": {"name": "Redundant Entry", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/redundant-entry.html"},
    "3.3.8": {"name": "Accessible Authentication (Minimum)", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/accessible-authentication-minimum.html"},

    # Principle 4: Robust
    "4.1.2": {"name": "Name, Role, Value", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "4.1.3": {"name": "Status Messages", "level": WCAGLevel.AA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},

    # WCAG 2.2 New Criteria
    "2.4.13": {"name": "Focus Appearance", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/focus-appearance.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "2.5.5": {"name": "Target Size (Enhanced)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "2.5.6": {"name": "Concurrent Input Mechanisms", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/concurrent-input-mechanisms.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "3.2.6": {"name": "Consistent Help", "level": WCAGLevel.A, "url": "https://www.w3.org/WAI/WCAG22/Understanding/consistent-help.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MODERATE},
    "3.3.9": {"name": "Accessible Authentication (Enhanced)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/accessible-authentication-enhanced.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},

    # Previously missing AAA criteria
    "1.2.6": {"name": "Sign Language (Prerecorded)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/sign-language-prerecorded.html"},
    "1.2.7": {"name": "Extended Audio Description (Prerecorded)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/extended-audio-description-prerecorded.html"},
    "1.2.8": {"name": "Media Alternative (Prerecorded)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/media-alternative-prerecorded.html"},
    "1.2.9": {"name": "Audio-only (Live)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/audio-only-live.html"},
    "1.3.6": {"name": "Identify Purpose", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/identify-purpose.html"},
    "1.4.6": {"name": "Contrast (Enhanced)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/contrast-enhanced.html", "fix_type": FixType.MANUAL, "severity": IssueSeverity.SERIOUS},
    "1.4.7": {"name": "Low or No Background Audio", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/low-or-no-background-audio.html"},
    "1.4.8": {"name": "Visual Presentation", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/visual-presentation.html"},
    "1.4.9": {"name": "Images of Text (No Exception)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/images-of-text-no-exception.html"},
    "2.1.3": {"name": "Keyboard (No Exception)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/keyboard-no-exception.html"},
    "2.2.3": {"name": "No Timing", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/no-timing.html"},
    "2.2.4": {"name": "Interruptions", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/interruptions.html"},
    "2.2.5": {"name": "Re-authenticating", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/re-authenticating.html"},
    "2.2.6": {"name": "Timeouts", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/timeouts.html"},
    "2.3.2": {"name": "Three Flashes", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/three-flashes.html"},
    "2.3.3": {"name": "Animation from Interactions", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html"},
    "2.4.8": {"name": "Location", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/location.html"},
    "2.4.9": {"name": "Link Purpose (Link Only)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-link-only.html"},
    "2.4.10": {"name": "Section Headings", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/section-headings.html"},
    "3.1.6": {"name": "Pronunciation", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/pronunciation.html"},
    "3.2.5": {"name": "Change on Request", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/change-on-request.html"},
    "3.3.5": {"name": "Help", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/help.html"},
    "3.3.6": {"name": "Error Prevention (All)", "level": WCAGLevel.AAA, "url": "https://www.w3.org/WAI/WCAG22/Understanding/error-prevention-all.html"},

    # Custom/Informational Criteria
    "HTML5_SEMANTICS": {"name": "HTML5 Semantic Elements", "level": WCAGLevel.A, "url": "#", "fix_type": FixType.MANUAL, "severity": IssueSeverity.INFO},
    "CSS_ANALYSIS": {"name": "CSS Rule Analysis", "level": WCAGLevel.AA, "url": "#", "fix_type": FixType.MANUAL, "severity": IssueSeverity.INFO},
    "HTML_VALIDATION": {"name": "HTML Validation (Standards)", "level": WCAGLevel.A, "url": "https://validator.w3.org/", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MINOR},
    "CSS_VALIDATION": {"name": "CSS Validation (Standards)", "level": WCAGLevel.A, "url": "https://jigsaw.w3.org/css-validator/", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MINOR},
    "SPELLING": {"name": "Spelling", "level": WCAGLevel.A, "url": "#", "fix_type": FixType.MANUAL, "severity": IssueSeverity.MINOR},
    "AXE_PASSED_GENERIC": {"name": "Axe-core Passed Rule", "level": WCAGLevel.AA, "url": "#", "fix_type": FixType.MANUAL, "severity": IssueSeverity.INFO},
    "INFO_CONTENT": {"name": "Content Analysis Info", "level": WCAGLevel.AAA, "url": "#", "fix_type": FixType.MANUAL, "severity": IssueSeverity.INFO},
    "UNKNOWN": {"name": "Unknown or General Accessibility Issue", "level": WCAGLevel.A, "url": "#", "fix_type": FixType.MANUAL, "severity": IssueSeverity.INFO},
}


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def get_criterion_details(criterion_id: str) -> Dict[str, Any]:
    """Get details for a WCAG criterion ID."""
    return WCAG_CRITERIA_DATABASE.get(criterion_id, WCAG_CRITERIA_DATABASE["UNKNOWN"])


# Section 508 (2017 Refresh) incorporates WCAG 2.0 Level A & AA by reference.
# These are the WCAG 2.0 A/AA success criteria; 2.1/2.2-only criteria are not formally part of 508.
SECTION_508_WCAG20 = {
    "1.1.1", "1.2.1", "1.2.2", "1.2.3", "1.3.1", "1.3.2", "1.3.3", "1.4.1", "1.4.2",
    "2.1.1", "2.1.2", "2.2.1", "2.2.2", "2.3.1", "2.4.1", "2.4.2", "2.4.3", "2.4.4",
    "3.1.1", "3.2.1", "3.2.2", "3.3.1", "3.3.2", "4.1.2",
    "1.2.4", "1.2.5", "1.4.3", "1.4.4", "1.4.5", "2.4.5", "2.4.6", "2.4.7",
    "3.1.2", "3.2.3", "3.2.4", "3.3.3", "3.3.4",
}


def is_section_508(criterion: str) -> bool:
    """True if the criterion is part of WCAG 2.0 A/AA (referenced by the Section 508 2017 Refresh)."""
    return criterion in SECTION_508_WCAG20


# ==============================================================================
# SECURITY HELPERS  (output encoding, SSRF guard, resource limits)
# ==============================================================================

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_SITEMAP_BYTES = 5 * 1024 * 1024
MAX_BROWSER_DOM_BYTES = 20 * 1024 * 1024
MAX_URL_LENGTH = 8192
DEFAULT_MAX_PDF_PAGES = 10000
DEFAULT_MAX_PDF_DOCUMENTS = 10000


def _safe_url(url, allow_relative: bool = True) -> str:
    """Return url only if it uses a safe scheme for a report link; else '#'.

    Blocks javascript:/data:/vbscript:/file: (XSS / local-file vectors), finding F1.
    """
    if not url:
        return "#"
    u = str(url).strip()
    if len(u) > MAX_URL_LENGTH or re.search(r'[\x00-\x1f\x7f]', u):
        return "#"
    low = re.sub(r'[\s\x00-\x1f]', '', u.lower())  # defeat whitespace/control-char smuggling
    if low.startswith(('javascript:', 'data:', 'vbscript:', 'file:')):
        return "#"
    if low.startswith(('http://', 'https://', 'mailto:', 'tel:')):
        return u
    if allow_relative and (u[:1] in ('/', '#', '?', '.') or ':' not in u.split('/', 1)[0]):
        return u  # relative path / fragment
    return "#"


def _csv_safe(value):
    """Neutralize CSV formula/macro injection (CWE-1236), finding F2."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def _safe_filename(name, maxlen: int = 80) -> str:
    """Reduce an arbitrary string to a filesystem-safe token (no path traversal), finding F7."""
    cleaned = re.sub(r'[^A-Za-z0-9._-]', '-', str(name)).lstrip('.')
    return (cleaned or 'x')[:maxlen]


def _md_cell(text) -> str:
    """Escape a value for safe inclusion in a Markdown table cell, finding F8."""
    s = str(text if text is not None else '').replace('\n', ' ').replace('\r', ' ')
    s = s.replace('|', '\\|').replace('`', '\\`').replace('<', '&lt;').replace('>', '&gt;')
    return ('\\' + s) if s[:1] in ('=', '+', '@') else s


def _ip_is_blocked(ip_str: str, allow_private: bool) -> bool:
    """True if an IP should not be connected to (SSRF guard), finding F5."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    metadata_addresses = {
        ipaddress.ip_address('169.254.169.254'),
        ipaddress.ip_address('100.100.100.200'),
        ipaddress.ip_address('fd00:ec2::254'),
    }
    if ip in metadata_addresses:
        return True
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if not allow_private and (ip.is_private or ip.is_loopback):
        return True
    return False


try:
    from aiohttp.abc import AbstractResolver as _AbstractResolver
    from aiohttp.resolver import ThreadedResolver as _ThreadedResolver
    _RESOLVER_OK = True
except Exception:
    _AbstractResolver, _ThreadedResolver, _RESOLVER_OK = object, None, False


class _SafeResolver(_AbstractResolver):
    """aiohttp resolver that refuses non-public IPs and also catches redirect hops."""

    def __init__(self, allow_private: bool = False):
        self._base = _ThreadedResolver()
        self.allow_private = allow_private

    async def resolve(self, host, port=0, family=socket.AF_INET):
        infos = await self._base.resolve(host, port, family)
        for info in infos:
            if _ip_is_blocked(info['host'], self.allow_private):
                raise OSError(f"Blocked connection to non-public address {info['host']} (host '{host}')")
        return infos

    async def close(self):
        await self._base.close()


def _safe_connector(allow_private: bool = False, **kwargs) -> "aiohttp.TCPConnector":
    """Build a TLS-verifying connector with the SSRF resolver attached (when available)."""
    if _RESOLVER_OK:
        return aiohttp.TCPConnector(resolver=_SafeResolver(allow_private), ssl=True, **kwargs)
    return aiohttp.TCPConnector(ssl=True, **kwargs)


async def _assert_safe_destination(url: str, allow_private: bool = False) -> None:
    """Reject unsafe schemes, credentials, IP literals, and resolved addresses."""
    if not url or len(str(url)) > MAX_URL_LENGTH or re.search(r'[\x00-\x20]', str(url)):
        raise ValueError("URL is empty, too long, or contains control characters")
    parsed = urlparse(str(url))
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError("Only HTTP and HTTPS URLs with a hostname are accepted")
    if parsed.username or parsed.password:
        raise ValueError("Credentials are not allowed in URLs")
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("URL contains an invalid port")

    hostname = parsed.hostname
    try:
        addresses = [str(ipaddress.ip_address(hostname))]
    except ValueError:
        try:
            infos = await asyncio.wait_for(
                asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM),
                timeout=5,
            )
        except Exception as exc:
            raise OSError(f"Could not safely resolve host '{hostname}': {exc}") from exc
        addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise OSError(f"Host '{hostname}' resolved to no addresses")
    blocked = [address for address in addresses if _ip_is_blocked(address, allow_private)]
    if blocked:
        raise OSError(f"Blocked connection to non-public or protected address for host '{hostname}'")


@asynccontextmanager
async def _safe_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    allow_private: bool = False,
    max_redirects: int = 5,
    **kwargs,
):
    """Issue a GET with explicit validation before every redirect hop."""
    current = str(url)
    response = None
    try:
        for hop in range(max_redirects + 1):
            await _assert_safe_destination(current, allow_private)
            response = await session.get(current, allow_redirects=False, **kwargs)
            if response.status not in {301, 302, 303, 307, 308}:
                yield response
                return
            if hop >= max_redirects:
                raise ValueError("Request exceeded the redirect limit")
            location = response.headers.get('Location')
            response.release()
            response = None
            if not location:
                raise ValueError("Redirect has no Location header")
            current = urljoin(current, location)
        raise ValueError("Request could not be completed")
    finally:
        if response is not None:
            response.release()


async def _read_capped_bytes(resp, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read a response body and reject content over the limit."""
    if resp.content_length and resp.content_length > max_bytes:
        raise ValueError(f"Response exceeds the {max_bytes}-byte safety limit")
    chunks: List[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Response exceeds the {max_bytes}-byte safety limit")
        chunks.append(chunk)
    return b''.join(chunks)


async def _read_capped_text(resp, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    """Read and decode a response body, rejecting content over the limit."""
    raw = await _read_capped_bytes(resp, max_bytes)
    enc = resp.charset or 'utf-8'
    try:
        return raw.decode(enc, errors='ignore')
    except (LookupError, TypeError):
        return raw.decode('utf-8', errors='ignore')


def _enum_value_safe(v):
    """Safely get enum value for serialization."""
    try:
        return v.value
    except Exception:
        return v


def generate_css_selector(element: Tag) -> str:
    """Generates a robust CSS selector for a BeautifulSoup Tag object."""
    if not isinstance(element, Tag):
        return ""

    path = []
    current = element
    while current and current.name and current.parent and current.name != '[document]':
        selector = current.name
        if current.has_attr('id') and current['id']:
            element_id = current['id'] if isinstance(current['id'], str) else ' '.join(current['id'])
            # Ensure ID is valid for CSS selector and doesn't contain spaces or special chars
            if re.match(r"^[a-zA-Z_][\w-]*$", element_id):
                selector = f"#{element_id}"
                path.append(selector)
                break # ID is unique enough

        classes = sorted([
            c for c in current.get('class', [])
            if c and isinstance(c, str) and re.match(r"^[a-zA-Z_][\w-]*$", c)
        ])
        if classes:
            selector += "." + ".".join(classes)

        # Add index if multiple siblings of the same tag name exist
        if current.parent:
            siblings = [sib for sib in current.parent.children if isinstance(sib, Tag) and sib.name == current.name]
            if len(siblings) > 1:
                try:
                    index = siblings.index(current) + 1
                    selector += f":nth-of-type({index})"
                except ValueError:
                    pass
        path.append(selector)
        current = current.parent
    return " > ".join(reversed(path))


def get_element_description(element: Union[Tag, str]) -> str:
    """Provides a concise string description of a BeautifulSoup Tag or string."""
    if not isinstance(element, Tag):
        return str(element)

    desc = f"<{element.name}"
    attrs = {k: v for k, v in element.attrs.items() if k in ['id', "class", 'name', 'type', 'href', 'alt', 'title']}
    for attr, val in attrs.items():
        val_str = ' '.join(val) if isinstance(val, list) else str(val)
        desc += f" {attr}='{val_str[:30]}...'" if len(val_str) > 30 else f" {attr}='{val_str}'"
    return desc + ">"


def get_element_context(soup: BeautifulSoup, element: Tag, lines: int = 5) -> str:
    """Gets a few lines of HTML context around the element for reporting."""
    if not element:
        return ""

    try:
        # Check if the element is actually part of this soup's parse tree
        element_in_soup = soup.find(lambda tag: tag is element)
        if not element_in_soup:
            return element.prettify() if isinstance(element, Tag) else str(element)

        # Generate a prettified version of the element and the whole soup
        elem_prettified = element_in_soup.prettify(formatter="html").strip()
        full_prettified = soup.prettify(formatter="html").strip()

        # Find the start of the element in the full prettified HTML
        element_start_idx = full_prettified.find(elem_prettified)
        if element_start_idx == -1:
            return elem_prettified

        # Calculate start and end line numbers for context
        all_lines = full_prettified.splitlines()
        element_start_line_num = full_prettified[:element_start_idx].count('\n')

        # Determine approx number of lines the element itself takes after prettify
        element_line_count = elem_prettified.count('\n') + 1

        context_start_line = max(0, element_start_line_num - (lines - 1) // 2)
        context_end_line = min(len(all_lines), element_start_line_num + element_line_count + lines // 2)

        return "\n".join(all_lines[context_start_line:context_end_line])
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Error getting element context: {e}. Falling back to element html.")
        return element.prettify() if isinstance(element, Tag) else str(element)


# Interactive ARIA roles whose elements are expected to expose an accessible name.
INTERACTIVE_ROLES = {
    "button", "link", "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "switch", "textbox", "combobox", "searchbox", "slider", "spinbutton", "option", "treeitem",
}


def _accname_text_from_ids(soup: BeautifulSoup, idref: str) -> str:
    """Concatenate the text of elements referenced by a space-separated id list."""
    parts = []
    for _id in (idref or "").split():
        ref = soup.find(id=_id)
        if ref:
            parts.append(ref.get_text(" ", strip=True))
    return " ".join(p for p in parts if p).strip()


def compute_accessible_name(el: Tag, soup: BeautifulSoup) -> str:
    """A practical subset of the W3C accessible name computation (this is ANDI's signature feature).

    Order: aria-labelledby -> aria-label -> native (label/legend/alt/value/text) -> title -> placeholder.
    """
    if not isinstance(el, Tag):
        return ""
    if el.has_attr('aria-labelledby'):
        name = _accname_text_from_ids(soup, el['aria-labelledby'])
        if name:
            return name
    if el.has_attr('aria-label') and str(el['aria-label']).strip():
        return str(el['aria-label']).strip()

    tag = el.name
    typ = (el.get('type') or '').lower()
    if tag in ('input', 'textarea', 'select'):
        if el.has_attr('id'):
            lbl = soup.find('label', attrs={'for': el['id']})
            if lbl and lbl.get_text(strip=True):
                return lbl.get_text(" ", strip=True)
        wrap = el.find_parent('label')
        if wrap and wrap.get_text(strip=True):
            return wrap.get_text(" ", strip=True)
        if tag == 'input' and typ in ('button', 'submit', 'reset') and (el.get('value') or '').strip():
            return el['value'].strip()
        if tag == 'input' and typ == 'image' and (el.get('alt') or '').strip():
            return el['alt'].strip()
    elif tag in ('img', 'area'):
        if el.has_attr('alt'):
            return el['alt'].strip()
    elif tag in ('button', 'a', 'summary') or el.has_attr('role'):
        txt = el.get_text(" ", strip=True)
        if not txt:
            txt = " ".join(i.get('alt', '').strip() for i in el.find_all('img') if i.get('alt', '').strip())
        if txt:
            return txt
    elif tag == 'fieldset':
        leg = el.find('legend')
        if leg and leg.get_text(strip=True):
            return leg.get_text(" ", strip=True)

    if el.has_attr('title') and str(el['title']).strip():
        return str(el['title']).strip()
    if el.has_attr('placeholder') and str(el['placeholder']).strip():
        return str(el['placeholder']).strip()
    return ""


class ColorUtils:
    """Utility class for color parsing and contrast ratio calculations."""

    @staticmethod
    def parse_color(color_str: str) -> Optional[Tuple[int, int, int]]:
        """Parse color string and return RGB tuple. Supports hex (3/4/6/8), rgb/rgba, hsl/hsla, modern space syntax, named colors."""
        if not color_str:
            return None
        color_str = color_str.lower().strip()

        if color_str in ('transparent', 'currentcolor', 'inherit', 'initial', 'unset'):
            return None

        if color_str.startswith('#'):
            hex_val = color_str.lstrip('#')
            # Handle 4-digit and 8-digit hex (with alpha)
            if len(hex_val) == 4:
                # #RGBA -> check alpha
                alpha = int(hex_val[3] * 2, 16) / 255.0
                if alpha < 0.05:
                    return None
                hex_val = hex_val[0] * 2 + hex_val[1] * 2 + hex_val[2] * 2
                return ColorUtils.hex_to_rgb('#' + hex_val)
            elif len(hex_val) == 8:
                # #RRGGBBAA -> check alpha
                alpha = int(hex_val[6:8], 16) / 255.0
                if alpha < 0.05:
                    return None
                return ColorUtils.hex_to_rgb('#' + hex_val[:6])
            return ColorUtils.hex_to_rgb(color_str)

        # Modern CSS rgb/rgba: supports both comma and space syntax
        rgb_match = re.match(r'rgba?\(\s*([\d.]+%?)\s*[,/\s]\s*([\d.]+%?)\s*[,/\s]\s*([\d.]+%?)(?:\s*[,/]\s*([\d.]+%?))?\s*\)', color_str)
        if rgb_match:
            def _parse_channel(val):
                if val.endswith('%'):
                    return int(float(val[:-1]) * 255 / 100)
                return min(255, max(0, int(float(val))))

            r, g, b = _parse_channel(rgb_match.group(1)), _parse_channel(rgb_match.group(2)), _parse_channel(rgb_match.group(3))
            if rgb_match.group(4):
                alpha_str = rgb_match.group(4)
                alpha = float(alpha_str[:-1]) / 100.0 if alpha_str.endswith('%') else float(alpha_str)
                if alpha < 0.05:
                    return None
            return (r, g, b)

        # Modern CSS hsl/hsla: supports both comma and space syntax
        hsl_match = re.match(r'hsla?\(\s*([\d.]+)\s*[,\s]\s*([\d.]+)%\s*[,\s]\s*([\d.]+)%(?:\s*[,/]\s*([\d.]+%?))?\s*\)', color_str)
        if hsl_match:
            h, s, l = float(hsl_match.group(1)), float(hsl_match.group(2)), float(hsl_match.group(3))
            if hsl_match.group(4):
                alpha_str = hsl_match.group(4)
                alpha = float(alpha_str[:-1]) / 100.0 if alpha_str.endswith('%') else float(alpha_str)
                if alpha < 0.05:
                    return None
            return ColorUtils.hsl_to_rgb(int(h), int(s), int(l))

        # Extended named colors (CSS Level 4)
        named_colors = {
            "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
            "blue": "#0000ff", "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080",
            "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "maroon": "#800000",
            "olive": "#808000", "lime": "#00ff00", "aqua": "#00ffff", "teal": "#008080",
            "navy": "#000080", "fuchsia": "#ff00ff", "cyan": "#00ffff", "magenta": "#ff00ff",
            "coral": "#ff7f50", "crimson": "#dc143c", "darkblue": "#00008b", "darkgreen": "#006400",
            "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "darkred": "#8b0000",
            "gold": "#ffd700", "indigo": "#4b0082", "ivory": "#fffff0",
            "khaki": "#f0e68c", "lavender": "#e6e6fa", "lightblue": "#add8e6",
            "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3", "lightgreen": "#90ee90",
            "lightyellow": "#ffffe0", "linen": "#faf0e6", "mintcream": "#f5fffa",
            "pink": "#ffc0cb", "plum": "#dda0dd", "salmon": "#fa8072",
            "skyblue": "#87ceeb", "slategray": "#708090", "slategrey": "#708090",
            "snow": "#fffafa", "tan": "#d2b48c", "tomato": "#ff6347",
            "turquoise": "#40e0d0", "violet": "#ee82ee", "wheat": "#f5deb3",
            "whitesmoke": "#f5f5f5", "yellowgreen": "#9acd32",
            "rebeccapurple": "#663399", "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7",
            "beige": "#f5f5dc", "bisque": "#ffe4c4", "brown": "#a52a2a",
            "burlywood": "#deb887", "cadetblue": "#5f9ea0", "chartreuse": "#7fff00",
            "chocolate": "#d2691e", "cornflowerblue": "#6495ed", "cornsilk": "#fff8dc",
            "darkkhaki": "#bdb76b", "darkorange": "#ff8c00", "darkorchid": "#9932cc",
            "darkviolet": "#9400d3", "deeppink": "#ff1493", "deepskyblue": "#00bfff",
            "dodgerblue": "#1e90ff", "firebrick": "#b22222", "forestgreen": "#228b22",
            "gainsboro": "#dcdcdc", "ghostwhite": "#f8f8ff", "goldenrod": "#daa520",
            "greenyellow": "#adff2f", "honeydew": "#f0fff0", "hotpink": "#ff69b4",
            "lawngreen": "#7cfc00", "lemonchiffon": "#fffacd", "lightcoral": "#f08080",
            "lightcyan": "#e0ffff", "lightpink": "#ffb6c1", "lightsalmon": "#ffa07a",
            "lightseagreen": "#20b2aa", "lightskyblue": "#87cefa", "lightsteelblue": "#b0c4de",
            "mediumaquamarine": "#66cdaa", "mediumblue": "#0000cd", "mediumorchid": "#ba55d3",
            "mediumpurple": "#9370db", "mediumseagreen": "#3cb371", "mediumslateblue": "#7b68ee",
            "mediumspringgreen": "#00fa9a", "mediumturquoise": "#48d1cc", "mediumvioletred": "#c71585",
            "midnightblue": "#191970", "mistyrose": "#ffe4e1", "moccasin": "#ffe4b5",
            "navajowhite": "#ffdead", "oldlace": "#fdf5e6", "olivedrab": "#6b8e23",
            "orangered": "#ff4500", "orchid": "#da70d6", "palegoldenrod": "#eee8aa",
            "palegreen": "#98fb98", "paleturquoise": "#afeeee", "palevioletred": "#db7093",
            "papayawhip": "#ffefd5", "peachpuff": "#ffdab9", "peru": "#cd853f",
            "powderblue": "#b0e0e6", "rosybrown": "#bc8f8f", "royalblue": "#4169e1",
            "saddlebrown": "#8b4513", "sandybrown": "#f4a460", "seagreen": "#2e8b57",
            "seashell": "#fff5ee", "sienna": "#a0522d", "springgreen": "#00ff7f",
            "steelblue": "#4682b4", "thistle": "#d8bfd8",
        }
        hex_color = named_colors.get(color_str)
        if hex_color:
            return ColorUtils.hex_to_rgb(hex_color)

        return None

    @staticmethod
    def hex_to_rgb(hex_color: str) -> Optional[Tuple[int, int, int]]:
        """Convert hex color to RGB tuple."""
        hex_color = hex_color.lstrip('#').lower()
        if len(hex_color) == 3:
            hex_color = "".join([c*2 for c in hex_color])
        if re.match(r"^[0-9a-f]{6}$", hex_color):
            try:
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                return (r, g, b)
            except ValueError:
                return None
        return None

    @staticmethod
    def hsl_to_rgb(h: int, s: int, l: int) -> Tuple[int, int, int]:
        """Convert HSL to RGB."""
        s /= 100.0
        l /= 100.0
        c = (1 - abs(2 * l - 1)) * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        m = l - c / 2
        r, g, b = 0, 0, 0

        if 0 <= h < 60:
            r, g, b = c, x, 0
        elif 60 <= h < 120:
            r, g, b = x, c, 0
        elif 120 <= h < 180:
            r, g, b = 0, c, x
        elif 180 <= h < 240:
            r, g, b = 0, x, c
        elif 240 <= h < 300:
            r, g, b = x, 0, c
        elif 300 <= h < 360:
            r, g, b = c, 0, x

        return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))

    @staticmethod
    def get_luminance(rgb_tuple: Tuple[int, int, int]) -> float:
        """Calculate relative luminance of RGB color."""
        if not rgb_tuple:
            return 0.0

        srgb = [val / 255.0 for val in rgb_tuple]
        r, g, b = [
            (val / 12.92) if val <= 0.03928 else ((val + 0.055) / 1.055) ** 2.4 for val in srgb
        ]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @staticmethod
    def get_contrast_ratio(color1_str: str, color2_str: str) -> float:
        """Calculate contrast ratio between two colors per WCAG 2.x formula."""
        rgb1 = ColorUtils.parse_color(color1_str)
        rgb2 = ColorUtils.parse_color(color2_str)
        if not rgb1 or not rgb2:
            return 1.0

        lum1 = ColorUtils.get_luminance(rgb1)
        lum2 = ColorUtils.get_luminance(rgb2)

        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)

        return (lighter + 0.05) / (darker + 0.05)


# ==============================================================================
# CONSTANTS
# ==============================================================================

APP_VERSION = "5.0.0"
APP_NAME = "WCAG 2.2 Site and PDF Scanner"
DEFAULT_USER_AGENT = f"WCAG-Site-PDF-Scanner/{APP_VERSION} (+accessibility testing)"



################################################################################
# BEGIN analyzers.py

# Optional NLP support. Importing the application never downloads data or makes
# network requests. Use the diagnostics command for explicit setup guidance.
try:
    import nltk
    from textstat import flesch_kincaid_grade, textstat
    NLTK_READY = True
    try:
        nltk.data.find('corpora/cmudict')
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        NLTK_READY = False
except ImportError:
    NLTK_READY = False

# Optional spelling support (SortSite-style spell check). Degrades gracefully if not installed.
try:
    from spellchecker import SpellChecker as _SpellChecker
    SPELL_READY = True
except Exception:
    _SpellChecker = None
    SPELL_READY = False

# Common technical/web terms that aren't in a standard dictionary (avoid spell-check false positives).
_SPELL_ALLOWLIST = {
    "http", "https", "www", "url", "urls", "html", "css", "javascript", "js", "json", "api", "apis",
    "faq", "faqs", "online", "website", "websites", "login", "logout", "username", "email", "emails",
    "blog", "blogs", "app", "apps", "pdf", "pdfs", "etc", "covid", "signup", "ecommerce", "chatbot",
    "config", "dropdown", "checkbox", "tooltip", "metadata", "namespace", "runtime", "lookup",
}

# Suppress cssutils warnings
cssutils.log.setLevel(logging.CRITICAL)


logger = logging.getLogger(__name__)
console = Console(record=True, width=150, log_path=False, log_time=False)
# BASE ANALYZER
# ==============================================================================

class BaseAnalyzer:
    """Base class for all analyzers, providing common methods for issue and passed check reporting."""

    def __init__(self, level: WCAGLevel):
        self.level = level
        self.issues: List[AccessibilityIssue] = []
        self.passed: List[PassedCheck] = []
        self.file_path: Optional[str] = None
        self.url: Optional[str] = None
        self.current_soup: Optional[BeautifulSoup] = None

    def _get_analysis_mode(self) -> AnalysisMode:
        """Get the appropriate AnalysisMode for this analyzer class."""
        class_name = self.__class__.__name__
        if class_name == "StaticAnalyzer":
            return AnalysisMode.STATIC
        elif class_name == "DynamicAnalyzer":
            return AnalysisMode.DYNAMIC
        elif class_name == "AxeAnalyzer":
            return AnalysisMode.AXE
        elif class_name == "CSSAnalyzer":
            return AnalysisMode.CSS
        elif class_name == "ContentAnalyzer":
            return AnalysisMode.CONTENT
        elif class_name == "ConsistencyAnalyzer":
            return AnalysisMode.CONSISTENCY
        elif class_name == "HTMLValidationAnalyzer":
            return AnalysisMode.VALIDATION
        elif class_name == "SpellingAnalyzer":
            return AnalysisMode.SPELLING
        else:
            return AnalysisMode.STATIC

    def _add_issue(self, criterion: str, severity: IssueSeverity, description: str, impact: str,
                   element: Optional[Union[Tag, str]] = None, selector: Optional[str] = None,
                   fix_type: Optional[FixType] = None, suggested_fix: Optional[str] = None,
                   additional_info: Dict[str, Any] = None, screenshot_filename: Optional[str] = None,
                   element_html: Optional[str] = None):
        """Add an accessibility issue to the report."""

        details = get_criterion_details(criterion)
        # Ensure issue level is relevant to the configured analysis level
        if not (details['level'] <= self.level):
            return

        element_html = element_html or (str(element) if element else None)
        context_html = None
        line_num = None

        # Only attempt to get context and line number if element is a BeautifulSoup Tag
        if isinstance(element, Tag) and self.current_soup and self.current_soup.body and element.name:
            context_html = get_element_context(self.current_soup, element)

            # Attempt to get line number for better context
            if hasattr(self, 'html_content_full') and self.html_content_full and element_html:
                try:
                    element_signature = element_html.splitlines()[0]
                    start_index = self.html_content_full.find(element_signature)
                    if start_index != -1:
                        line_num = self.html_content_full[:start_index].count('\n') + 1
                except Exception as e:
                    logger.debug(f"Failed to find element signature for line numbering: {e}")

        element_desc = get_element_description(element) if element else None
        mode_enum = self._get_analysis_mode()

        self.issues.append(AccessibilityIssue(
            criterion=criterion,
            criterion_name=details['name'],
            level=details['level'],
            severity=severity,
            mode=mode_enum,
            description=description,
            impact=impact,
            element=element_desc,
            element_html=element_html,
            context_html=context_html,
            selector=selector or (generate_css_selector(element) if isinstance(element, Tag) else None),
            fix_type=fix_type or details.get('fix_type', FixType.MANUAL),
            suggested_fix=suggested_fix or details.get('suggested_fix'),
            file_path=self.file_path,
            url=self.url,
            line_number=line_num,
            screenshot_path=screenshot_filename,
            additional_info=additional_info or {}
        ))

    def _add_passed(self, criterion: str, description: str, elements_checked: int = 0,
                    details: Optional[str] = None, url: Optional[str] = None):
        """Record a narrow automated check with no detected failure."""

        details_from_db = get_criterion_details(criterion)
        if not (details_from_db['level'] <= self.level):
            return

        mode_enum = self._get_analysis_mode()

        self.passed.append(PassedCheck(
            criterion=criterion,
            criterion_name=details_from_db['name'],
            level=details_from_db['level'],
            mode=mode_enum,
            description=description,
            elements_checked=elements_checked,
            file_path=self.file_path,
            url=url or self.url,
            details=details
        ))


# ==============================================================================
# STATIC ANALYZER
# ==============================================================================

class StaticAnalyzer(BaseAnalyzer):
    """Performs static analysis on HTML source code using BeautifulSoup."""

    def __init__(self, level: WCAGLevel):
        super().__init__(level)
        self.html_content_full: Optional[str] = None

    def analyze(self, html_content: str, url_or_path: str) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        """Analyze HTML content statically."""
        self.issues, self.passed = [], []
        self.html_content_full = html_content

        if url_or_path.startswith('http'):
            self.url = url_or_path
            self.file_path = None
        else:
            self.file_path = url_or_path
            self.url = None

        self.current_soup = BeautifulSoup(html_content, 'lxml')

        # Dynamically call all methods starting with '_check_'
        for method_name in dir(self):
            if method_name.startswith('_check_'):
                check_method = getattr(self, method_name)
                if inspect.ismethod(check_method) and check_method.__qualname__.startswith(self.__class__.__name__):
                    try:
                        if not self.current_soup.find():
                            if html_content.strip():
                                logger.debug(f"Skipping static checks for {url_or_path}: HTML content is empty or malformed after parsing.")
                            break

                        doc = inspect.getdoc(check_method)
                        if doc:
                            match = re.search(r"Criterion (\d[\.\d]+) \(Level (A+)\)", doc)
                            if match:
                                criterion_level_str = match.group(2)
                                criterion_level = WCAGLevel(criterion_level_str)

                                if criterion_level <= self.level:
                                    check_method()
                            else:
                                logger.debug(f"Skipping static check {method_name}: No WCAG criterion found in docstring.")
                        else:
                            logger.debug(f"Skipping static check {method_name}: No docstring found.")
                    except Exception as e:
                        logger.error(f"Error running static check {method_name} on {url_or_path}: {e}")

        return self.issues, self.passed

    def _check_1_1_1_image_alt(self):
        """Criterion 1.1.1 (Level A): Checks for missing/empty alt on img, area, input[type=image]."""
        elements = (
            self.current_soup.find_all(['img', 'area'])
            + self.current_soup.find_all('input', attrs={'type': 'image'})
        )

        for el in elements:
            in_figure_with_figcaption = False
            parent_figure = el.find_parent('figure')
            if parent_figure and parent_figure.find('figcaption'):
                in_figure_with_figcaption = True

            is_decorative_role = el.get('role') in ['presentation', 'none']

            if el.name == 'img' or el.name == 'area':
                if not el.has_attr('alt'):
                    if not is_decorative_role:
                        severity = IssueSeverity.CRITICAL
                        description_base = f"<{el.name}> element is missing an 'alt' attribute."
                        suggested_fix_base = "Add an `alt` attribute. Use `alt=\"\"` for decorative images or provide a concise description."

                        if in_figure_with_figcaption:
                            severity = IssueSeverity.MODERATE
                            description_base += " It is inside a `<figure>` with a `<figcaption>`, so the description might be external."
                            suggested_fix_base = "Add an `alt` attribute, or ensure `<figcaption>` adequately describes the image. Use `alt=\"\"` for decorative images."

                        self._add_issue("1.1.1", severity, description_base,
                            "Users of assistive technologies cannot understand the content or purpose of the image.",
                            element=el, suggested_fix=suggested_fix_base)

                elif el['alt'].strip() == "":
                    if not is_decorative_role and (el.has_attr('title') and el['title'].strip() != "") \
                        and not in_figure_with_figcaption:
                        self._add_issue("1.1.1", IssueSeverity.MODERATE,
                            f"<{el.name} alt=''> element has empty alt but a non-empty title or is likely informative.",
                            "Empty alt text hides intended content for screen reader users if meant to be informative.",
                            element=el, suggested_fix='Ensure `alt=""` if decorative, otherwise provide descriptive alt text.')

                elif is_decorative_role and el['alt'].strip() != "":
                    self._add_issue("1.1.1", IssueSeverity.MODERATE,
                        f"Decorative <{el.name}> (role='presentation' or 'none') has non-empty alt text.",
                        "Assistive technologies may unnecessarily announce the alt text of a decorative image.",
                        element=el, suggested_fix='Set `alt=""` for decorative images or remove the role if the image is actually informative.')

            elif el.name == 'input' and el.get('type') == 'image':
                if not el.has_attr('alt') or not el['alt'].strip():
                    self._add_issue("1.1.1", IssueSeverity.CRITICAL,
                        "<input type='image'> is missing a descriptive 'alt' attribute.",
                        "Screen reader users cannot understand the button's purpose without descriptive alt text.",
                        element=el, suggested_fix="Add an 'alt' attribute that describes the button's function.")

        self._add_passed("1.1.1", "Checked <img>, <area>, <input type='image'> for 'alt' attributes.", elements_checked=len(elements))

    def _check_1_3_1_info_and_relationships(self):
        """Criterion 1.3.1 (Level A): Checks semantic structure: headings, tables, lists, ARIA roles, fieldsets."""
        # Headings: check for h1 presence and logical order
        headings = self.current_soup.find_all(re.compile(r'^h[1-6]$'))
        if not headings:
            self._add_issue("1.3.1", IssueSeverity.SERIOUS,
                "No heading elements (h1-h6) found on the page.",
                "Users of assistive technologies rely on headings to understand page structure and navigate content.",
                element=self.current_soup.body, selector="body", suggested_fix="Add appropriate heading structure to the page.")
        else:
            first_h1 = self.current_soup.find('h1')
            if not first_h1:
                self._add_issue("1.3.1", IssueSeverity.MODERATE,
                    "Page does not contain an <h1> heading.",
                    "Missing an <h1> can make it difficult for screen reader users to grasp the main topic of the page.",
                    element=headings[0] if headings else self.current_soup.body,
                    suggested_fix="Ensure the primary heading of the page is an <h1>.")

            last_level = 0
            for h in headings:
                current_level = int(h.name[1])
                if last_level > 0 and current_level > last_level + 1:
                    self._add_issue("1.3.1", IssueSeverity.MODERATE,
                        f"Skipped heading level: <h{current_level}> follows <h{last_level}>.",
                        "Improper heading order can confuse screen reader users and impair navigation by headings.",
                        element=h, suggested_fix="Restructure headings to follow a logical, sequential order (e.g., h1 -> h2 -> h3).")
                last_level = current_level

        self._add_passed("1.3.1", "Heading structure checked.", elements_checked=len(headings))

        # Tables: check for proper use of <th>, <caption>, scope
        tables = self.current_soup.find_all('table')
        for table in tables:
            if table.get('role') in ['presentation', 'none']:
                self._add_passed("1.3.1", "Table marked as presentational (role='presentation') was skipped from data table checks.",
                               elements_checked=1, details=get_element_description(table))
                continue

            headers = table.find_all('th')
            if not headers and table.find('td'):
                self._add_issue("1.3.1", IssueSeverity.SERIOUS,
                    "Data table is missing header cells (<th>).",
                    "Screen reader users cannot understand data cell relationships without headers.",
                    element=table, suggested_fix="Use <th> for headers with a `scope` attribute ('col' or 'row'), or add `role='presentation'` if it's purely for layout.")

            if not table.find('caption') and not table.get('summary') and len(table.find_all('tr')) > 2:
                self._add_issue("1.3.1", IssueSeverity.MODERATE,
                    "Complex table lacks a `<caption>` or `summary` attribute.",
                    "Provides a concise description for users of assistive technologies, especially for complex tables.",
                    element=table, suggested_fix="Add a `<caption>` element as the first child of the `<table>` to describe its purpose.")

            if headers:
                for th in headers:
                    header_id = th.get('id')
                    has_explicit_reference = bool(
                        header_id and table.find(
                            lambda td, expected_id=header_id: td.has_attr('headers')
                            and expected_id in td['headers'].split()
                        )
                    )
                    if not th.has_attr('scope') and not has_explicit_reference:
                        self._add_issue("1.3.1", IssueSeverity.MINOR,
                            "Table header cell (<th>) is missing a `scope` attribute or `id` for relationship.",
                            "Explicitly defining scope helps screen readers associate headers with data cells.",
                            element=th, suggested_fix="Add `scope='col'` or `scope='row'` to `<th>` elements.")

        self._add_passed("1.3.1", "Data tables checked for headers and captions.", elements_checked=len(tables))

        # Fieldset and Legend for grouped form controls
        for input_type in ['radio', 'checkbox']:
            inputs = self.current_soup.find_all('input', {'type': input_type})
            names = {r.get('name') for r in inputs if r.get('name')}
            for name in names:
                group = [i for i in inputs if i.get('name') == name]
                if len(group) > 1:
                    has_fieldset_legend = False
                    for control in group:
                        parent_fieldset = control.find_parent('fieldset')
                        if parent_fieldset:
                            legend = parent_fieldset.find('legend')
                            if legend and legend.get_text(strip=True):
                                has_fieldset_legend = True
                                break
                            else:
                                if not legend:
                                    self._add_issue("1.3.1", IssueSeverity.SERIOUS,
                                        f"<fieldset> for '{name}' group is missing a <legend>.",
                                        "A <legend> is essential to provide a programmatic group label for related form controls.",
                                        element=parent_fieldset, suggested_fix="Add a descriptive <legend> tag inside the <fieldset>.")
                                elif not legend.get_text(strip=True):
                                    self._add_issue("1.3.1", IssueSeverity.MODERATE,
                                        f"<fieldset> for '{name}' group has an empty <legend>.",
                                        "The legend should provide a meaningful group label for related controls.",
                                        element=legend, suggested_fix="Add descriptive text to the <legend> tag.")
                                has_fieldset_legend = True
                                break

                    if not has_fieldset_legend:
                        self._add_issue("1.3.1", IssueSeverity.SERIOUS,
                            f"Group of '{input_type}' controls with name '{name}' is not contained within a <fieldset> with a <legend>.",
                            "Screen readers may not announce the controls as a related group with a clear question.",
                            element=group[0], suggested_fix="Wrap related inputs in a <fieldset> and provide a descriptive <legend>.")

        self._add_passed("1.3.1", "Form control groups checked for fieldset/legend.", elements_checked=len(inputs) if 'inputs' in locals() else 0)

        # Proper list usage
        for ul_ol in self.current_soup.find_all(['ul', 'ol']):
            for child in ul_ol.children:
                if isinstance(child, Tag) and child.name not in ['li', 'template', 'script', 'style']:
                    self._add_issue("1.3.1", IssueSeverity.MODERATE,
                        f"Non-<li> element ('<{child.name}>') found directly inside <{ul_ol.name}>.",
                        "Only <li> elements are valid direct children of <ul> and <ol> according to HTML specifications.",
                        element=child, suggested_fix="Ensure only <li> elements are direct children of <ul> or <ol>.")

        for dl_list in self.current_soup.find_all('dl'):
            for child in dl_list.children:
                if isinstance(child, Tag) and child.name not in ['dt', 'dd', 'template', 'script', 'style']:
                    self._add_issue("1.3.1", IssueSeverity.MODERATE,
                        f"Non-<dt> or <dd> element ('<{child.name}>') found directly inside <dl>.",
                        "Only <dt> and <dd> elements are valid direct children of <dl>.",
                        element=child, suggested_fix="Ensure only <dt> or <dd> elements are direct children of <dl>.")

        self._add_passed("1.3.1", "List structures checked for proper element usage.", elements_checked=len(self.current_soup.find_all(['ul', 'ol', 'dl'])))

    def _check_1_3_5_identify_input_purpose(self):
        """Criterion 1.3.5 (Level AA): Checks for autocomplete on common form fields."""
        common_autocompletes = {
            "name", "honorific-prefix", "given-name", "additional-name", "family-name", "honorific-suffix",
            "nickname", "username", "new-password", "current-password", "one-time-code",
            "organization", "organization-title", "street-address", "address-line1", "address-line2",
            "address-line3", "address-level4", "address-level3", "address-level2", "address-level1",
            "country", "country-name", "postal-code",
            "cc-name", "cc-given-name", "cc-additional-name", "cc-family-name", "cc-number", "cc-exp",
            "cc-exp-month", "cc-exp-year", "cc-csc", "cc-type",
            "email", "url", "tel", "tel-national", "tel-area-code", "tel-local", "impp", "fax",
            "sex", "bday", "bday-day", "bday-month", "bday-year", "gender", "language", "photo",
            "transaction-amount", "transaction-currency", "shipping", "billing"
        }

        excluded_input_types = {'hidden', 'submit', 'reset', 'button', 'checkbox', 'radio', 'image'}
        inputs = self.current_soup.find_all(
            lambda tag: (tag.name in ('textarea', 'select')) or
                        (tag.name == 'input' and tag.get('type') not in excluded_input_types)
        )

        for inp in inputs:
            attrs_for_guess = (inp.get('name', '') + inp.get('id', '') + inp.get('placeholder', '') + inp.get('type', '')).lower()

            potential_autocomplete_value = "on"
            for term in sorted(common_autocompletes, key=len, reverse=True):
                if term.replace('-', '') in attrs_for_guess.replace('-', ''):
                    potential_autocomplete_value = term
                    break

            if not inp.has_attr('autocomplete') or not inp['autocomplete'] or inp['autocomplete'].strip() == "":
                self._add_issue("1.3.5", IssueSeverity.MODERATE,
                    f"Input field ({get_element_description(inp)}) is missing an 'autocomplete' attribute or it's empty.",
                    "Autofill helps users with cognitive or mobility disabilities by reducing typing effort and entry errors.",
                    element=inp, suggested_fix=f"Add an `autocomplete` attribute. Based on heuristics, `autocomplete='{potential_autocomplete_value}'` might be suitable.",
                    additional_info={'suggested_autocomplete': potential_autocomplete_value})
            elif inp['autocomplete'].lower() not in common_autocompletes:
                self._add_issue("1.3.5", IssueSeverity.MINOR,
                    f"Input field ({get_element_description(inp)}) has a non-standard 'autocomplete' value: '{inp['autocomplete']}'.",
                    "Using non-standard 'autocomplete' values may prevent browsers from offering appropriate autofill suggestions.",
                    element=inp, suggested_fix="Verify the 'autocomplete' value adheres to the HTML standard.")

        self._add_passed("1.3.5", "Checked form inputs for 'autocomplete' attribute.", elements_checked=len(inputs))

    def _check_2_4_1_bypass_blocks(self):
        """Criterion 2.4.1 (Level A): Checks for a 'skip to main content' link and main landmark."""
        body = self.current_soup.find('body')
        if not body:
            return

        main_content_landmark = self.current_soup.find('main') or self.current_soup.find(role='main')
        if not main_content_landmark:
            self._add_issue("2.4.1", IssueSeverity.MODERATE,
                "No `<main>` element or `role='main'` landmark found.",
                "Assistive technology users benefit from `main` landmark to quickly navigate to primary content.",
                element=self.current_soup.body, suggested_fix="Wrap the primary content of the page in a `<main>` element or set `role='main'` on its container.",
                fix_type=FixType.MANUAL) # This requires human judgment.

        skip_link_found = False
        potential_skip_links = self.current_soup.find_all('a', href=re.compile(r'^#'))

        for link in potential_skip_links:
            link_text = link.get_text(strip=True).lower()
            if 'skip' in link_text or 'main content' in link_text or 'skip-link' in link.get('class', []):
                skip_link_found = True
                target_id = link['href'].lstrip('#')
                if not self.current_soup.find(id=target_id):
                    self._add_issue("2.4.1", IssueSeverity.SERIOUS,
                        f"Skip link '{link_text}' points to a non-existent ID '#{target_id}'.",
                        "The skip link will not function, preventing keyboard users from bypassing repetitive content.",
                        element=link, suggested_fix=f"Ensure the target element with `id='{target_id}'` exists.",
                        fix_type=FixType.MANUAL) # Can't know what the correct ID should be.
                break

        if not skip_link_found:
            self._add_issue("2.4.1", IssueSeverity.SERIOUS,
                "A 'skip to main content' link was not found or is not easily accessible.",
                "Keyboard users must tab through all navigation on every page to reach the main content.",
                element=self.current_soup.body, suggested_fix="Add a link near the top of the `<body>` like `<a href='#main-content'>Skip to main content</a>`.",
                fix_type=FixType.SEMI_AUTOMATIC)

        self._add_passed("2.4.1", "Checked for bypass block mechanisms (skip link, main landmark).", elements_checked=1)

    def _check_2_4_2_page_titled(self):
        """Criterion 2.4.2 (Level A): Checks for a non-empty <title> element."""
        title_tag = self.current_soup.find('title')
        if not title_tag or not title_tag.string or not title_tag.string.strip():
            self._add_issue("2.4.2", IssueSeverity.CRITICAL,
                "The page is missing a <title> element or the title is empty.",
                "Users cannot identify the page's purpose in browser tabs, history, or bookmarks.",
                element=self.current_soup.head or self.current_soup,
                suggested_fix="Add a descriptive, unique <title> inside the <head> element.")
        else:
            self._add_passed("2.4.2", "A valid page title was found.", elements_checked=1)

    def _check_2_4_4_link_purpose(self):
        """Criterion 2.4.4 (Level A): Checks for ambiguous link text without sufficient context."""
        ambiguous_phrases = {'click here', 'learn more', 'read more', 'more', 'here', 'link', 'continue', 'download'}
        links = self.current_soup.find_all('a', href=True)

        for link in links:
            accessible_name = link.get_text(strip=True).lower()
            if not accessible_name:
                accessible_name = link.get('aria-label', '').lower()
            if not accessible_name:
                accessible_name = link.get('title', '').lower()

            if accessible_name in ambiguous_phrases:
                self._add_issue("2.4.4", IssueSeverity.SERIOUS,
                    f"Link text or accessible name is ambiguous: '{link.get_text(strip=True) or link.get('aria-label') or link.get('title') or 'Empty Link'}'.",
                    "Screen reader users navigating by a list of links will not understand the link's purpose out of context.",
                    element=link, suggested_fix="Rewrite link text to be descriptive of its destination or provide a descriptive `aria-label`.")

            if link.find('img'):
                img = link.find('img')
                if (not img.has_attr('alt') or not img['alt'].strip()) and \
                   (not link.get_text(strip=True) and not link.get('aria-label')):
                    self._add_issue("2.4.4", IssueSeverity.CRITICAL,
                        f"Image link has no accessible text or image alt text: ({get_element_description(link)}).",
                        "Users cannot determine the purpose of a link composed only of an image without alternative text.",
                        element=link, suggested_fix="Add meaningful `alt` text to the image, or descriptive text within the `<a>` tag or an `aria-label` attribute.")

        self._add_passed("2.4.4", "Checked link text for ambiguity and accessible names.", elements_checked=len(links))

    def _check_2_4_6_iframe_title(self):
        """Criterion 2.4.6 (Level AA): Checks that <iframe> elements have a non-empty title."""
        iframes = self.current_soup.find_all('iframe')
        for frame in iframes:
            if not frame.get('title', '').strip():
                self._add_issue("2.4.6", IssueSeverity.SERIOUS,
                    "<iframe> element is missing a descriptive title attribute.",
                    "Users of assistive technologies may not understand the content or purpose of the embedded frame.",
                    element=frame, suggested_fix="Add a `title` attribute that accurately describes the iframe's contents.",
                    fix_type=FixType.SEMI_AUTOMATIC)

        self._add_passed("2.4.6", "Checked <iframe> elements for titles.", elements_checked=len(iframes))

    def _check_3_1_1_language_of_page(self):
        """Criterion 3.1.1 (Level A): Checks for the 'lang' attribute on <html>."""
        html_tag = self.current_soup.find('html')
        if not html_tag or not html_tag.has_attr('lang') or not html_tag['lang'].strip():
            self._add_issue("3.1.1", IssueSeverity.CRITICAL,
                "The 'lang' attribute is missing or empty on the <html> element.",
                "Assistive technologies cannot determine the page language, causing mispronunciations.",
                element=self.current_soup.html or self.current_soup,
                suggested_fix="Add a valid language attribute, such as `lang='en'`, to the <html> tag.",
                fix_type=FixType.SEMI_AUTOMATIC)
        else:
            lang_code = html_tag['lang'].strip()
            # BCP 47: language[-script][-region][-variant] e.g. en, en-US, zh-Hans-CN, pt-BR
            if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z]{4})?(-[a-zA-Z]{2}|-\d{3})?(-([a-zA-Z\d]{5,8}|\d[a-zA-Z\d]{3}))*$", lang_code, re.IGNORECASE):
                self._add_issue("3.1.1", IssueSeverity.MODERATE,
                    f"The 'lang' attribute '{html_tag['lang']}' on the <html> element is not a well-formed BCP 47 language tag.",
                    "Invalid language tags may cause assistive technologies to fail in correctly interpreting the page's language.",
                    element=html_tag, suggested_fix="Use a valid BCP 47 language tag (e.g., 'en', 'en-US', 'es').")
            else:
                self._add_passed("3.1.1", "Primary language declared on <html>.", elements_checked=1)

    def _check_3_3_2_labels_or_instructions(self):
        """Criterion 3.3.2 (Level A): Checks that form controls have accessible labels."""
        controls = self.current_soup.find_all(['input', 'textarea', 'select'])
        for control in controls:
            if control.get('type') in ['hidden', 'submit', 'reset', 'button', 'image', 'checkbox', 'radio']:
                continue

            has_accessible_label = False
            control_id = control.get('id')

            # 1. Explicit <label for="id"> connection
            if control_id:
                matching_label = self.current_soup.find('label', {'for': control_id})
                if matching_label and matching_label.get_text(strip=True):
                    has_accessible_label = True

            # 2. Implicit <label><input></label> wrapping
            if not has_accessible_label and control.find_parent('label'):
                parent_label = control.find_parent('label')
                if parent_label.get_text(strip=True):
                    has_accessible_label = True

            # 3. ARIA labels
            if not has_accessible_label and (control.has_attr('aria-label') and control['aria-label'].strip() != ""):
                has_accessible_label = True

            if not has_accessible_label and (control.has_attr('aria-labelledby') and control['aria-labelledby'].strip() != ""):
                labelledby_ids = control['aria-labelledby'].split()
                if all(self.current_soup.find(id=lid) for lid in labelledby_ids):
                    has_accessible_label = True

            # 4. Placeholder as fallback (discouraged)
            if not has_accessible_label and (control.has_attr('placeholder') and control['placeholder'].strip() != ""):
                self._add_issue("3.3.2", IssueSeverity.MODERATE,
                    "Form control uses `placeholder` as its only label. Placeholders disappear on input and are not reliably announced.",
                    "Hinders accessibility for users with cognitive disabilities or those using assistive technologies.",
                    element=control, suggested_fix="Provide a persistent and programmatically associated `<label>` or `aria-label` attribute.")
                has_accessible_label = True

            if not has_accessible_label:
                self._add_issue("3.3.2", IssueSeverity.CRITICAL,
                    "Form control is not programmatically associated with an accessible label.",
                    "Screen reader users will not know what data to enter into the form field.",
                    element=control, suggested_fix="Provide a `<label>` with a `for` attribute matching the input's `id`, wrap the input in a `<label>` tag, or use an `aria-label` attribute.",
                    fix_type=FixType.SEMI_AUTOMATIC)

        self._add_passed("3.3.2", "Checked form controls for accessible labels.", elements_checked=len(controls))

    def _check_4_1_2_name_role_value(self):
        """Criterion 4.1.2 (Level A): Checks for valid ARIA roles/properties and proper ARIA usage."""
        elements_with_aria = self.current_soup.find_all(lambda tag: any(attr.startswith(('role', 'aria-')) for attr in tag.attrs))

        for el in elements_with_aria:
            # 1. Check for invalid ARIA roles
            if el.has_attr('role'):
                roles = el['role'] if isinstance(el['role'], list) else el['role'].split()
                for role in roles:
                    if role not in VALID_ARIA_ROLES:
                        self._add_issue("4.1.2", IssueSeverity.SERIOUS,
                            f"Element has an invalid ARIA role: '{role}'.",
                            "Assistive technologies will not understand the element's purpose.",
                            element=el, suggested_fix=f"Replace '{role}' with a valid ARIA role from the WAI-ARIA specification.")

            # 2. Check for invalid aria-* attributes
            for attr in el.attrs:
                if attr.startswith('aria-'):
                    if attr not in VALID_ARIA_PROPS:
                        self._add_issue("4.1.2", IssueSeverity.SERIOUS,
                            f"Element has an invalid ARIA attribute: '{attr}'.",
                            "Assistive technologies may ignore this attribute or behave unpredictably.",
                            element=el, suggested_fix=f"Verify the spelling of '{attr}'. Ensure it is a valid ARIA state or property.")

                    # 3. Check for aria-labelledby/describedby pointing to non-existent IDs
                    if attr in ['aria-labelledby', 'aria-describedby', 'aria-owns', 'aria-controls']:
                        referenced_ids = el[attr].split() if isinstance(el[attr], str) else (el[attr] if isinstance(el[attr], list) else [])
                        for target_id in referenced_ids:
                            if not self.current_soup.find(id=target_id):
                                self._add_issue("4.1.2", IssueSeverity.CRITICAL,
                                    f"'{attr}' attribute references non-existent ID: '{target_id}'.",
                                    "Screen readers will fail to find the associated label/description.",
                                    element=el, suggested_fix=f"Ensure `id='{target_id}'` exists and is correctly spelled.")

            # 4. ARIA best practices: aria-hidden on focusable elements
            if el.get('aria-hidden') == 'true':
                is_focusable = el.name in ['a', 'button', 'input', 'select', 'textarea'] or \
                               (el.has_attr('tabindex') and el['tabindex'].strip() != '-1')

                if is_focusable:
                    self._add_issue("4.1.2", IssueSeverity.CRITICAL,
                        f"Focusable element ({get_element_description(el)}) has `aria-hidden='true'`.",
                        "Hides the element from assistive technology users, but physical keyboard focus is still possible.",
                        element=el, suggested_fix="Remove `aria-hidden='true'` if it should be accessible, or remove the element's focusability.")

            # 5. Required ARIA attributes for roles
            if el.has_attr('role'):
                roles = el['role'] if isinstance(el['role'], list) else el['role'].split()
                for role in roles:
                    required_props = ARIA_REQUIRED_PROPS.get(role)
                    if required_props:
                        for prop in required_props:
                            if not el.has_attr(prop):
                                self._add_issue("4.1.2", IssueSeverity.SERIOUS,
                                    f"Element with `role='{role}'` is missing a required ARIA attribute: `{prop}`.",
                                    "Screen readers may not correctly convey the state or functionality of this interactive component.",
                                    element=el, suggested_fix=f"Add the `{prop}` attribute with an appropriate value for `role='{role}'`.")

        self._add_passed("4.1.2", "Checked ARIA roles and properties for validity and best practices.", elements_checked=len(elements_with_aria))

    def _check_4_1_3_status_messages(self):
        """Criterion 4.1.3 (Level AA): Checks that status messages use appropriate ARIA live regions."""
        # Check for common status message patterns without aria-live or appropriate roles
        status_roles = {'status', 'alert', 'log', 'progressbar', 'timer'}
        live_regions = self.current_soup.find_all(attrs={'aria-live': True})
        role_regions = self.current_soup.find_all(attrs={'role': lambda v: v in status_roles if v else False})

        # Check forms for error containers that lack live region markup
        forms = self.current_soup.find_all('form')
        for form in forms:
            error_containers = form.find_all(class_=re.compile(r'error|alert|message|feedback|notification|toast', re.I))
            for container in error_containers:
                has_live = container.get('aria-live') or container.get('role') in status_roles
                if not has_live:
                    parent_live = container.find_parent(attrs={'aria-live': True}) or \
                                  container.find_parent(attrs={'role': lambda v: v in status_roles if v else False})
                    if not parent_live:
                        self._add_issue("4.1.3", IssueSeverity.SERIOUS,
                            f"Potential status message container (class: {container.get('class')}) lacks `aria-live` or an appropriate ARIA role.",
                            "Screen reader users will not be notified when status messages appear or change.",
                            element=container,
                            suggested_fix="Add `role='alert'` for urgent messages, `role='status'` for advisory info, or `aria-live='polite'`/`aria-live='assertive'` as appropriate.")

        total_checked = len(live_regions) + len(role_regions) + len(forms)
        self._add_passed("4.1.3", "Checked for ARIA live region usage on status messages.", elements_checked=total_checked)

    def _check_1_2_1_audio_video_alternatives(self):
        """Criterion 1.2.1 (Level A): Checks that audio-only and video-only prerecorded content has alternatives."""
        audio_elements = self.current_soup.find_all('audio')
        video_elements = self.current_soup.find_all('video')

        for audio in audio_elements:
            has_track = audio.find('track')
            # Check for nearby transcript link
            parent = audio.parent
            has_transcript = False
            if parent:
                links = parent.find_all('a', href=True)
                for link in links:
                    link_text = link.get_text(strip=True).lower()
                    if any(word in link_text for word in ['transcript', 'text version', 'text alternative']):
                        has_transcript = True
                        break
            if not has_track and not has_transcript:
                self._add_issue("1.2.1", IssueSeverity.SERIOUS,
                    "Audio element lacks a text transcript or <track> element.",
                    "Deaf or hard-of-hearing users cannot access audio-only content without a text alternative.",
                    element=audio, suggested_fix="Provide a text transcript near the audio element, or add a <track> element.")

        self._add_passed("1.2.1", "Checked audio/video elements for alternatives.", elements_checked=len(audio_elements) + len(video_elements))

    def _check_1_2_2_captions_prerecorded(self):
        """Criterion 1.2.2 (Level A): Checks that prerecorded video with audio has captions."""
        videos = self.current_soup.find_all('video')
        for video in videos:
            caption_track = video.find('track', kind='captions') or video.find('track', kind='subtitles')
            if not caption_track:
                self._add_issue("1.2.2", IssueSeverity.SERIOUS,
                    "Video element is missing captions (<track kind='captions'>).",
                    "Deaf or hard-of-hearing users cannot understand the audio content of the video.",
                    element=video, suggested_fix="Add `<track kind='captions' src='captions.vtt' srclang='en' label='English'>` inside the <video>.")

        self._add_passed("1.2.2", "Checked video elements for caption tracks.", elements_checked=len(videos))

    def _check_1_2_5_audio_description(self):
        """Criterion 1.2.5 (Level AA): Checks that prerecorded video has audio descriptions."""
        videos = self.current_soup.find_all('video')
        for video in videos:
            desc_track = video.find('track', kind='descriptions')
            if not desc_track:
                self._add_issue("1.2.5", IssueSeverity.MODERATE,
                    "Video element is missing an audio description track (<track kind='descriptions'>).",
                    "Blind users may not understand visual-only content in the video.",
                    element=video, suggested_fix="Add `<track kind='descriptions' src='descriptions.vtt'>` or provide a separate audio-described version.")

        self._add_passed("1.2.5", "Checked video elements for audio description tracks.", elements_checked=len(videos))

    def _check_1_3_4_orientation(self):
        """Criterion 1.3.4 (Level AA): Checks that content does not restrict display orientation."""
        # Check for CSS orientation lock indicators in style blocks
        style_blocks = self.current_soup.find_all('style')
        for style in style_blocks:
            if style.string:
                style_text = style.string.lower()
                if 'orientation:' in style_text and ('portrait' in style_text or 'landscape' in style_text):
                    if 'transform: rotate' in style_text or 'display: none' in style_text:
                        self._add_issue("1.3.4", IssueSeverity.SERIOUS,
                            "CSS may be restricting content to a single display orientation.",
                            "Users with mobility disabilities who mount devices in a fixed orientation cannot use orientation-locked content.",
                            suggested_fix="Remove CSS that hides or rotates content based on orientation. Use @media (orientation:...) only for layout adaptation, not restriction.")

        # Viewport metadata does not itself provide a reliable orientation-lock signal.
        self._add_passed("1.3.4", "Checked for orientation restriction.", elements_checked=1)

    def _check_1_4_2_audio_control(self):
        """Criterion 1.4.2 (Level A): Checks that auto-playing audio can be paused/stopped."""
        # Check for autoplay on audio and video elements
        auto_media = self.current_soup.find_all(['audio', 'video'], attrs={'autoplay': True})
        for media in auto_media:
            has_controls = media.has_attr('controls')
            is_muted = media.has_attr('muted')
            if not has_controls and not is_muted:
                self._add_issue("1.4.2", IssueSeverity.CRITICAL,
                    f"<{media.name} autoplay> element plays automatically without `controls` or `muted` attribute.",
                    "Screen reader users' audio output is drowned out by auto-playing audio, making the page unusable.",
                    element=media, suggested_fix="Add `controls` attribute so users can pause/stop, or add `muted` to prevent audio interference.")

        self._add_passed("1.4.2", "Checked audio/video elements for autoplay controls.", elements_checked=len(auto_media))

    def _check_1_4_4_resize_text(self):
        """Criterion 1.4.4 (Level AA): Checks for text that cannot be resized (fixed px sizes, viewport lock)."""
        meta_viewport = self.current_soup.find('meta', attrs={'name': 'viewport'})
        if meta_viewport:
            content = (meta_viewport.get('content') or '').lower()
            if 'maximum-scale=1' in content.replace(' ', '') or 'user-scalable=no' in content.replace(' ', ''):
                self._add_issue("1.4.4", IssueSeverity.SERIOUS,
                    "Viewport meta tag restricts user scaling (maximum-scale=1 or user-scalable=no).",
                    "Users with low vision who need to zoom/enlarge text are prevented from doing so.",
                    element=meta_viewport, suggested_fix="Remove `maximum-scale=1` and `user-scalable=no` from the viewport meta tag.")

        self._add_passed("1.4.4", "Checked viewport meta for text resize restrictions.", elements_checked=1)

    def _check_2_1_4_character_key_shortcuts(self):
        """Criterion 2.1.4 (Level A): Checks for single-character key shortcuts via accesskey."""
        elements_with_accesskey = self.current_soup.find_all(attrs={'accesskey': True})
        for el in elements_with_accesskey:
            # bs4 can return an AttributeValueList for accesskey; coerce to a plain string.
            accesskey = str(el.get('accesskey') or '').strip()
            if len(accesskey) == 1 and accesskey.isalpha():
                self._add_issue("2.1.4", IssueSeverity.MODERATE,
                    f"Element uses a single-character `accesskey='{accesskey}'` shortcut.",
                    "Single-character shortcuts can be accidentally triggered by speech input users, causing unintended actions.",
                    element=el, suggested_fix="Ensure the shortcut can be remapped or turned off, or is only active when the component has focus.")

        self._add_passed("2.1.4", "Checked for single-character key shortcuts.", elements_checked=len(elements_with_accesskey))

    def _check_2_2_1_timing_adjustable(self):
        """Criterion 2.2.1 (Level A): Checks for meta refresh that could impose time limits."""
        meta_refresh = self.current_soup.find('meta', attrs={'http-equiv': re.compile(r'refresh', re.I)})
        if meta_refresh:
            content = meta_refresh.get('content', '')
            # Parse the timeout value
            timeout_match = re.match(r'(\d+)', content.strip())
            if timeout_match:
                timeout = int(timeout_match.group(1))
                if timeout > 0 and timeout < 72001:  # Less than 20 hours
                    has_redirect = ';' in content and 'url=' in content.lower()
                    issue_type = "redirect" if has_redirect else "refresh"
                    self._add_issue("2.2.1", IssueSeverity.CRITICAL,
                        f"Page has a meta {issue_type} after {timeout} seconds.",
                        "Users with disabilities may not have enough time to read or interact with content before the page changes.",
                        element=meta_refresh, suggested_fix="Remove automatic refresh/redirect, or provide a mechanism to extend, adjust, or turn off the time limit.")

        self._add_passed("2.2.1", "Checked for meta refresh timing.", elements_checked=1)

    def _check_2_2_2_pause_stop_hide(self):
        """Criterion 2.2.2 (Level A): Checks for moving, blinking, scrolling, or auto-updating content."""
        # Check for <marquee> and <blink> (deprecated but still found)
        marquees = self.current_soup.find_all('marquee')
        for m in marquees:
            self._add_issue("2.2.2", IssueSeverity.SERIOUS,
                "Deprecated <marquee> element found. Moving content cannot be paused.",
                "Users with cognitive disabilities, attention disorders, or vestibular disorders may find moving content distracting or nauseating.",
                element=m, suggested_fix="Replace <marquee> with static content or CSS animation that respects `prefers-reduced-motion`.")

        blinks = self.current_soup.find_all('blink')
        for b in blinks:
            self._add_issue("2.2.2", IssueSeverity.SERIOUS,
                "Deprecated <blink> element found. Blinking content cannot be stopped.",
                "Blinking content is distracting and can cause seizures in some users.",
                element=b, suggested_fix="Remove <blink> element entirely and use static styling.")

        self._add_passed("2.2.2", "Checked for <marquee> and <blink> elements.", elements_checked=len(marquees) + len(blinks))

    def _check_2_4_5_multiple_ways(self):
        """Criterion 2.4.5 (Level AA): Checks that multiple ways to locate pages are available."""
        body = self.current_soup.find('body')
        if not body:
            return

        has_nav = bool(self.current_soup.find('nav') or self.current_soup.find(role='navigation'))
        has_search = bool(self.current_soup.find('form', role='search') or
                         self.current_soup.find('input', attrs={'type': 'search'}) or
                         self.current_soup.find(role='search'))
        has_sitemap_link = False
        for link in self.current_soup.find_all('a', href=True):
            if 'sitemap' in link.get_text(strip=True).lower() or 'site map' in link.get_text(strip=True).lower():
                has_sitemap_link = True
                break

        ways_count = sum([has_nav, has_search, has_sitemap_link])
        if ways_count < 2:
            self._add_issue("2.4.5", IssueSeverity.MODERATE,
                f"Page may not provide multiple ways to locate content (found {ways_count} mechanism(s)).",
                "Users with different abilities and preferences need multiple ways to find content (navigation, search, sitemap, etc.).",
                element=body, suggested_fix="Provide at least two of: navigation menu, search functionality, sitemap link, table of contents.")

        self._add_passed("2.4.5", "Checked for multiple navigation mechanisms.", elements_checked=1)

    def _check_2_5_3_label_in_name(self):
        """Criterion 2.5.3 (Level A): Checks that visible labels are part of accessible names."""
        # Check buttons and links where visible text differs from aria-label
        elements = self.current_soup.find_all(['button', 'a'], attrs={'aria-label': True})
        for el in elements:
            visible_text = el.get_text(strip=True).lower()
            aria_label = (el.get('aria-label') or '').lower()
            if visible_text and aria_label and visible_text not in aria_label:
                self._add_issue("2.5.3", IssueSeverity.SERIOUS,
                    f"Visible label '{el.get_text(strip=True)[:30]}' is not contained within `aria-label='{el.get('aria-label')[:30]}'`.",
                    "Speech input users who speak the visible label cannot activate the control because the accessible name differs.",
                    element=el, suggested_fix="Ensure `aria-label` contains the exact visible text. Ideally, remove `aria-label` if the visible text is sufficient.")

        # Check inputs with aria-label that differs from associated <label>
        inputs = self.current_soup.find_all(['input', 'textarea', 'select'], attrs={'aria-label': True})
        for inp in inputs:
            inp_id = inp.get('id')
            if inp_id:
                label = self.current_soup.find('label', attrs={'for': inp_id})
                if label:
                    label_text = label.get_text(strip=True).lower()
                    aria_label = (inp.get('aria-label') or '').lower()
                    if label_text and aria_label and label_text not in aria_label:
                        self._add_issue("2.5.3", IssueSeverity.SERIOUS,
                            f"Input label text '{label.get_text(strip=True)[:30]}' is not in `aria-label='{inp.get('aria-label')[:30]}'`.",
                            "Speech input users cannot activate this control by speaking the visible label.",
                            element=inp, suggested_fix="Ensure `aria-label` includes the visible label text, or remove `aria-label` to use the <label> as the accessible name.")

        self._add_passed("2.5.3", "Checked visible labels match accessible names.", elements_checked=len(elements) + len(inputs))

    def _check_3_1_2_language_of_parts(self):
        """Criterion 3.1.2 (Level AA): Checks that language changes within content are marked with lang attributes."""
        # Check for elements with lang attribute and validate them
        elements_with_lang = self.current_soup.find_all(attrs={'lang': True})
        for el in elements_with_lang:
            if el.name == 'html':
                continue  # Handled by 3.1.1
            lang_val = el.get('lang', '').strip()
            if lang_val and not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z]{4})?(-[a-zA-Z]{2}|-\d{3})?$", lang_val, re.IGNORECASE):
                self._add_issue("3.1.2", IssueSeverity.MODERATE,
                    f"Element has an invalid `lang` attribute value: '{lang_val}'.",
                    "Assistive technologies may mispronounce content marked with an invalid language code.",
                    element=el, suggested_fix="Use a valid BCP 47 language tag (e.g., 'fr', 'es', 'de').")

        self._add_passed("3.1.2", "Checked language of parts for valid lang attributes.", elements_checked=len(elements_with_lang))

    def _check_3_3_1_error_identification(self):
        """Criterion 3.3.1 (Level A): Checks that required form fields are properly identified."""
        # Check required inputs without aria-required or required attribute announcement
        required_inputs = self.current_soup.find_all(
            lambda tag: tag.name in ('input', 'textarea', 'select') and
            (tag.has_attr('required') or tag.get('aria-required') == 'true')
        )

        # Check for aria-invalid usage without associated error messages
        invalid_elements = self.current_soup.find_all(attrs={'aria-invalid': 'true'})
        for el in invalid_elements:
            error_msg_id = el.get('aria-errormessage') or el.get('aria-describedby')
            if error_msg_id:
                for ref_id in error_msg_id.split():
                    if not self.current_soup.find(id=ref_id):
                        self._add_issue("3.3.1", IssueSeverity.SERIOUS,
                            f"Element with `aria-invalid='true'` references non-existent error message ID '{ref_id}'.",
                            "Screen reader users will not hear the error description when the field is invalid.",
                            element=el, suggested_fix=f"Ensure an element with `id='{ref_id}'` exists and contains the error message.")

        self._add_passed("3.3.1", "Checked error identification on form fields.", elements_checked=len(required_inputs) + len(invalid_elements))

    def _check_3_3_7_redundant_entry(self):
        """Criterion 3.3.7 (Level A): Checks for potential redundant entry in multi-step forms."""
        # Heuristic: check if the same autocomplete values appear multiple times in same form
        forms = self.current_soup.find_all('form')
        for form in forms:
            autocomplete_values = []
            inputs = form.find_all(['input', 'textarea', 'select'])
            for inp in inputs:
                ac = inp.get('autocomplete', '').strip().lower()
                if ac and ac not in ('off', 'on', ''):
                    autocomplete_values.append(ac)

            duplicates = [v for v in set(autocomplete_values) if autocomplete_values.count(v) > 1]
            for dup in duplicates:
                self._add_issue("3.3.7", IssueSeverity.MODERATE,
                    f"Form contains multiple fields with the same autocomplete value '{dup}', which may require redundant entry.",
                    "Users with motor or cognitive disabilities find re-entering the same information burdensome.",
                    element=form, suggested_fix="Auto-populate repeated fields or allow users to select from previously entered data.")

        self._add_passed("3.3.7", "Checked for redundant entry patterns in forms.", elements_checked=len(forms))

    def _check_3_3_8_accessible_authentication(self):
        """Criterion 3.3.8 (Level AA): Checks that authentication doesn't rely solely on cognitive function tests."""
        # Heuristic: check for CAPTCHA elements or patterns
        captcha_patterns = self.current_soup.find_all(class_=re.compile(r'captcha|recaptcha|hcaptcha|g-recaptcha', re.I))
        captcha_iframes = self.current_soup.find_all('iframe', src=re.compile(r'captcha|recaptcha|hcaptcha', re.I))

        all_captchas = captcha_patterns + captcha_iframes
        for captcha in all_captchas:
            self._add_issue("3.3.8", IssueSeverity.SERIOUS,
                "CAPTCHA detected which may impose a cognitive function test for authentication.",
                "Users with cognitive disabilities may be unable to complete CAPTCHA challenges.",
                element=captcha, suggested_fix="Provide an alternative authentication method that doesn't require solving a CAPTCHA, such as email-based login or passkeys.")

        # Check login forms for password-only auth without paste support
        password_inputs = self.current_soup.find_all('input', attrs={'type': 'password'})
        for pwd in password_inputs:
            if pwd.get('autocomplete') == 'off':
                self._add_issue("3.3.8", IssueSeverity.MODERATE,
                    "Password field has `autocomplete='off'`, which prevents password managers from autofilling.",
                    "Users who rely on password managers (to avoid remembering passwords) are blocked from using them.",
                    element=pwd, suggested_fix="Remove `autocomplete='off'` or set to `autocomplete='current-password'` to allow password manager support.")

        self._add_passed("3.3.8", "Checked authentication patterns for cognitive function tests.", elements_checked=len(all_captchas) + len(password_inputs))

    def _check_2_4_3_focus_order(self):
        """Criterion 2.4.3 (Level A): Flags positive tabindex values that force a manual focus order."""
        all_tabindexed = self.current_soup.find_all(attrs={'tabindex': True})
        for el in all_tabindexed:
            try:
                ti = int(str(el.get('tabindex')).strip())
            except (TypeError, ValueError):
                continue
            if ti > 0:
                self._add_issue("2.4.3", IssueSeverity.MODERATE,
                    f"<{el.name}> uses a positive tabindex ({ti}), overriding the natural focus order.",
                    "Positive tabindex values reorder keyboard focus away from DOM order and frequently produce a sequence that does not match the visual/reading order, confusing keyboard and screen-reader users.",
                    element=el, suggested_fix="Use `tabindex=\"0\"` (focusable in natural order) or `tabindex=\"-1\"` (focusable only via script). Control order through DOM order, not positive values.")
        self._add_passed("2.4.3", "Checked for positive tabindex values affecting focus order.", elements_checked=len(all_tabindexed))

    def _check_1_2_3_media_alternative(self):
        """Criterion 1.2.3 (Level A): Checks prerecorded video has an audio-description track or a text/media alternative."""
        videos = self.current_soup.find_all('video')
        for video in videos:
            has_desc_track = any(
                (t.get('kind') or '').lower() in ('descriptions', 'description')
                for t in video.find_all('track')
            )
            container = video.find_parent(['figure', 'section', 'article', 'div']) or video.parent
            nearby_text = container.get_text(" ", strip=True).lower() if container else ""
            has_alt_reference = video.has_attr('aria-describedby') or any(
                k in nearby_text for k in ('transcript', 'text alternative', 'described', 'audio description')
            )
            if not has_desc_track and not has_alt_reference:
                self._add_issue("1.2.3", IssueSeverity.MODERATE,
                    "Prerecorded <video> has no audio-description track and no detectable text/media alternative.",
                    "People who cannot see the video lose information conveyed visually unless an audio description or an equivalent text alternative is provided.",
                    element=video, suggested_fix="Provide an audio-description track (`<track kind=\"descriptions\">`) or a full transcript/media alternative that conveys the visual information, referenced near the media.")
        self._add_passed("1.2.3", "Checked prerecorded video for audio description or media alternative.", elements_checked=len(videos))

    def _check_2_4_9_link_purpose_link_only(self):
        """Criterion 2.4.9 (Level AAA): Flags links whose text alone does not describe their purpose."""
        vague = {"click here", "here", "read more", "more", "learn more", "link", "this", "this page",
                 "details", "click", "go", "continue", "more info", "more information", "view", "download",
                 "see more", "find out more", "get started"}
        links = self.current_soup.find_all('a', href=True)
        for a in links:
            # The accessible name (aria-label) takes precedence over visible text for AT.
            text = (a.get('aria-label') or '').strip() or a.get_text(strip=True)
            norm = re.sub(r'\s+', ' ', text).strip().lower().rstrip('.!:›»→')
            if not norm:
                continue  # empty link names are covered by 2.4.4 / 4.1.2
            if norm in vague:
                self._add_issue("2.4.9", IssueSeverity.MODERATE,
                    f"Link text \"{text[:40]}\" does not describe the link's purpose on its own.",
                    "Level AAA (2.4.9) requires a link's purpose to be clear from its text alone, helping screen-reader users who navigate via an out-of-context list of links.",
                    element=a, suggested_fix="Use self-describing link text (e.g., 'Download the 2026 benefits guide (PDF)'), or supply an equivalent `aria-label`.")
        self._add_passed("2.4.9", "Checked that link text is descriptive on its own.", elements_checked=len(links))

    def _check_4_1_2_accessible_names(self):
        """Criterion 4.1.2 (Level A): Interactive elements need a non-empty accessible name (ANDI-style)."""
        soup = self.current_soup
        seen, interactive = set(), []
        candidates = soup.find_all('button') + [a for a in soup.find_all('a') if a.has_attr('href')]
        candidates += [i for i in soup.find_all('input') if (i.get('type') or '').lower() in ('button', 'submit', 'reset', 'image')]
        candidates += [el for el in soup.find_all(attrs={'role': True})
                       if el.get('role') in INTERACTIVE_ROLES and el.name not in ('input', 'select', 'textarea')]
        for el in candidates:
            if id(el) in seen:
                continue
            seen.add(id(el))
            interactive.append(el)

        for el in interactive:
            # aria-hidden on a focusable element is a "phantom focus" danger ANDI flags
            if el.get('aria-hidden') == 'true' or el.find_parent(attrs={'aria-hidden': 'true'}):
                self._add_issue("4.1.2", IssueSeverity.SERIOUS,
                    f"Focusable <{el.name}> is hidden from assistive technology (aria-hidden='true').",
                    "A keyboard-focusable element hidden with aria-hidden becomes a phantom stop: focus lands on something with no announced name or role.",
                    element=el, suggested_fix="Remove aria-hidden from focusable elements, or make it non-focusable (tabindex='-1') if it should be hidden.")
                continue
            if not compute_accessible_name(el, soup):
                self._add_issue("4.1.2", IssueSeverity.SERIOUS,
                    f"<{el.name}> has no accessible name (nothing for a screen reader to announce).",
                    "Screen reader users hear the role but no name, so they cannot tell what the control does or where it leads.",
                    element=el, suggested_fix="Provide an accessible name via visible text, aria-label, or aria-labelledby (for image buttons, use alt).")
        self._add_passed("4.1.2", "Computed accessible names for links/buttons/controls (ANDI-style).", elements_checked=len(interactive))

    def _check_4_1_2_aria_idrefs(self):
        """Criterion 4.1.2 (Level A): ARIA id references must resolve to elements that exist (ANDI-style)."""
        soup = self.current_soup
        existing = set()
        for el in soup.find_all(attrs={'id': True}):
            _id = el.get('id'); _id = ' '.join(_id) if isinstance(_id, list) else _id
            if _id:
                existing.add(_id)
        checked = 0
        for attr in ('aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-owns', 'aria-details'):
            for el in soup.find_all(attrs={attr: True}):
                checked += 1
                missing = [r for r in str(el.get(attr) or '').split() if r and r not in existing]
                if missing:
                    self._add_issue("4.1.2", IssueSeverity.SERIOUS,
                        f"{attr} on <{el.name}> references id(s) that don't exist: {', '.join(missing[:5])}.",
                        "When an ARIA relationship points to a missing id, the intended name/description/relationship is silently lost for assistive technology.",
                        element=el, suggested_fix=f"Make every id in {attr} match an element actually present on the page.")
        self._add_passed("4.1.2", "Checked ARIA id references resolve to existing elements.", elements_checked=checked)

    def _check_4_1_2_redundant_title(self):
        """Criterion 4.1.2 (Level A): Flags a title attribute that merely duplicates the visible text (ANDI caution)."""
        soup = self.current_soup
        checked = 0
        for el in soup.find_all(attrs={'title': True}):
            title = str(el.get('title') or '').strip()
            if not title or el.has_attr('aria-label') or el.has_attr('aria-labelledby'):
                continue
            checked += 1
            txt = el.get_text(" ", strip=True)
            if txt and title.lower() == txt.lower():
                self._add_issue("4.1.2", IssueSeverity.MINOR,
                    f"Redundant title attribute duplicates the visible text: \"{title[:40]}\".",
                    "A title that just repeats the visible text adds tooltip noise and is announced twice by some screen readers.",
                    element=el, suggested_fix="Remove the redundant title, or use it for genuinely supplementary information.")
        self._add_passed("4.1.2", "Checked for redundant title attributes.", elements_checked=checked)

    def _check_1_1_1_alt_quality(self):
        """Criterion 1.1.1 (Level A): Flags filenames or generic placeholder alt text (ANDI-style)."""
        placeholders = {"image", "images", "photo", "photos", "picture", "pic", "graphic", "graphics",
                        "logo", "icon", "spacer", "img", "banner", "thumbnail"}
        imgs = self.current_soup.find_all('img')
        for img in imgs:
            if not img.has_attr('alt'):
                continue
            alt = img['alt'].strip()
            if not alt:
                continue
            low = alt.lower()
            if re.search(r'\.(jpe?g|png|gif|svg|webp|bmp|tiff?)(\?|$)', low) or re.match(r'^(dsc|img|image|screenshot|photo)[-_ ]?\d+', low):
                self._add_issue("1.1.1", IssueSeverity.MODERATE,
                    f"Image alt text looks like a filename: \"{alt[:40]}\".",
                    "A filename conveys nothing meaningful to screen reader users.",
                    element=img, suggested_fix="Describe the image's content/purpose, or use alt=\"\" if it is decorative.")
            elif low in placeholders:
                self._add_issue("1.1.1", IssueSeverity.MODERATE,
                    f"Image alt text is a generic placeholder: \"{alt}\".",
                    "Generic words like 'image' or 'logo' don't describe the content or function.",
                    element=img, suggested_fix="Describe what the image shows or does (e.g., the organization name for a logo).")
        self._add_passed("1.1.1", "Checked alt-text quality (filenames / placeholders).", elements_checked=len(imgs))

    def _check_1_3_1_landmarks(self):
        """Criterion 1.3.1 (Level A): Checks one main and no duplicate banner/contentinfo landmarks (ANDI-style)."""
        soup = self.current_soup

        def count_landmark(tag, role):
            explicit = soup.find_all(attrs={'role': role})
            native = soup.find_all(tag) if tag else []
            if tag in ('header', 'footer'):
                native = [e for e in native if not e.find_parent(['article', 'section', 'main', 'aside', 'nav'])]
            return len({id(e) for e in list(explicit) + list(native)})

        mains = count_landmark('main', 'main')
        banners = count_landmark('header', 'banner')
        contentinfos = count_landmark('footer', 'contentinfo')
        body = soup.body
        if mains == 0:
            self._add_issue("1.3.1", IssueSeverity.MODERATE, "Page has no main landmark (<main> or role='main').",
                "A main landmark lets assistive-technology users skip straight to the primary content.",
                element=body, selector="body", suggested_fix="Wrap the primary content in a single <main> element.")
        elif mains > 1:
            self._add_issue("1.3.1", IssueSeverity.MODERATE, f"Page has {mains} main landmarks; there must be exactly one.",
                "Multiple main landmarks are invalid and confuse landmark navigation.",
                element=body, selector="body", suggested_fix="Keep a single <main>/role='main' per page.")
        if banners > 1:
            self._add_issue("1.3.1", IssueSeverity.MODERATE, f"Page has {banners} top-level banner landmarks; use at most one.",
                "Multiple top-level banners make orientation harder for AT users.",
                element=body, selector="body", suggested_fix="Use a single top-level <header>/role='banner'.")
        if contentinfos > 1:
            self._add_issue("1.3.1", IssueSeverity.MODERATE, f"Page has {contentinfos} top-level contentinfo landmarks; use at most one.",
                "Multiple top-level contentinfo landmarks make orientation harder for AT users.",
                element=body, selector="body", suggested_fix="Use a single top-level <footer>/role='contentinfo'.")
        self._add_passed("1.3.1", "Checked landmark regions (main/banner/contentinfo).", elements_checked=mains + banners + contentinfos)

    def _check_1_3_1_empty_structural(self):
        """Criterion 1.3.1 (Level A): Flags empty headings and empty table header cells (ANDI-style)."""
        soup = self.current_soup
        headings = soup.find_all(re.compile(r'^h[1-6]$'))
        for h in headings:
            if not h.get_text(strip=True) and not h.find('img', alt=True):
                self._add_issue("1.3.1", IssueSeverity.MODERATE, f"Empty <{h.name}> heading.",
                    "Empty headings clutter the screen-reader heading list and convey no structure.",
                    element=h, suggested_fix="Add heading text, or remove the empty heading element.")
        ths = soup.find_all('th')
        for th in ths:
            if not th.get_text(strip=True) and not th.find('img', alt=True):
                self._add_issue("1.3.1", IssueSeverity.MODERATE, "Empty table header cell (<th>).",
                    "An empty header leaves its associated data cells without a meaningful header for screen readers.",
                    element=th, suggested_fix="Provide header text in the <th> (or restructure if it's a layout gap).")
        self._add_passed("1.3.1", "Checked for empty headings and table headers.", elements_checked=len(headings) + len(ths))

    def _check_2_1_1_duplicate_accesskey(self):
        """Criterion 2.1.1 (Level A): Flags duplicate accesskey values (ANDI-style)."""
        soup = self.current_soup
        keys = {}
        for el in soup.find_all(attrs={'accesskey': True}):
            k = str(el.get('accesskey') or '').strip().lower()
            if k:
                keys.setdefault(k, []).append(el)
        for k, els in keys.items():
            if len(els) > 1:
                self._add_issue("2.1.1", IssueSeverity.MODERATE,
                    f"Duplicate accesskey '{k}' assigned to {len(els)} elements.",
                    "When one accesskey is bound to multiple elements, the keyboard shortcut becomes ambiguous and unreliable.",
                    element=els[0], suggested_fix=f"Assign each accesskey to at most one element ('{k}' is reused).")
        self._add_passed("2.1.1", "Checked accesskey uniqueness.", elements_checked=sum(len(v) for v in keys.values()))


# ==============================================================================
# CSS ANALYZER
# ==============================================================================

class CSSAnalyzer(BaseAnalyzer):
    """Performs static analysis on CSS content."""

    def __init__(self, level: WCAGLevel):
        super().__init__(level)

    def analyze(self, css_content: str, url_or_path: str) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        """Analyze CSS content for accessibility issues."""
        self.issues, self.passed = [], []

        if url_or_path.startswith('http'):
            self.url = url_or_path
            self.file_path = None
        else:
            self.file_path = url_or_path
            self.url = None

        # Capture cssutils parser messages (leak-free) for CSS validation (Standards) reporting.
        # setLog with a non-propagating logger keeps these out of stderr during a large scan.
        _val_buf = io.StringIO()
        _val_handler = logging.StreamHandler(_val_buf)
        _val_handler.setLevel(logging.WARNING)
        _cap_logger = logging.getLogger("wscan.cssutils.capture")
        _cap_logger.handlers = [_val_handler]
        _cap_logger.setLevel(logging.WARNING)
        _cap_logger.propagate = False
        cssutils.log.setLog(_cap_logger)
        try:
            stylesheet = cssutils.parseString(css_content)
        except Exception as e:
            logger.debug(f"Error parsing CSS content from {url_or_path}: {e}")
            self._add_issue("CSS_ANALYSIS", IssueSeverity.INFO,
                f"Failed to parse CSS content from '{url_or_path}'.",
                "Parsing errors prevent comprehensive CSS accessibility checks.",
                element=css_content[:100] + "...",
                additional_info={'parser_error': str(e)})
            return self.issues, self.passed
        finally:
            cssutils.log.setLevel(logging.CRITICAL)

        # CSS validation (Standards), only when enabled by the orchestrator.
        if getattr(self, 'validate_css', False):
            seen_msgs = set()
            for line in _val_buf.getvalue().splitlines():
                msg = line.strip()
                if not msg or msg in seen_msgs or 'No content to parse' in msg:
                    continue
                seen_msgs.add(msg)
                if len(seen_msgs) > 25:
                    break
                self._add_issue("CSS_VALIDATION", IssueSeverity.MINOR,
                    f"CSS validation issue: {msg[:200]}",
                    "Invalid or unknown CSS may be silently ignored by browsers, causing inconsistent presentation.",
                    selector=url_or_path,
                    suggested_fix="Correct the CSS so it parses cleanly (cf. the W3C CSS validator).")

        for rule in stylesheet.cssRules:
            if rule.type == CSSStyleRule.STYLE_RULE:
                for prop in rule.style.getProperties():
                    try:
                        self._check_property(rule.selectorText, prop, rule)
                    except Exception as e:
                        logger.error(f"Error checking CSS property '{prop.name}' in rule '{rule.selectorText}': {e}")
            elif isinstance(rule, cssutils.css.CSSMediaRule):
                for media_rule_inner in rule.cssRules:
                    if media_rule_inner.type == CSSStyleRule.STYLE_RULE:
                        media_selector = f"@media {rule.media.mediaText} {{ {media_rule_inner.selectorText} }}"
                        for prop in media_rule_inner.style.getProperties():
                            try:
                                self._check_property(media_selector, prop, media_rule_inner)
                            except Exception as e:
                                logger.error(f"Error checking CSS property '{prop.name}' in media rule: {e}")

        if stylesheet.cssRules:
            self._add_passed("CSS_ANALYSIS", "CSS properties analyzed for common accessibility pitfalls.", elements_checked=len(stylesheet.cssRules))

        return self.issues, self.passed

    def _check_property(self, selector: str, prop: Property, rule: CSSStyleRule):
        """Check individual CSS property for accessibility issues."""

        # 1.4.3 Contrast checking (limited static analysis)
        if self.level >= WCAGLevel.AA:
            if prop.name == 'color':
                bg_prop = rule.style.getProperty('background-color')
                if bg_prop:
                    fg = prop.value
                    bg = bg_prop.value
                    try:
                        ratio = ColorUtils.get_contrast_ratio(fg, bg)
                        if ratio > 1.0 and ratio < 4.5:
                            self._add_issue("1.4.3", IssueSeverity.CRITICAL,
                                f"Potential low contrast: Text color '{fg}' against background '{bg}' "
                                f"(Ratio: {ratio:.2f}:1) in CSS rule '{selector}'.",
                                "Users with low vision may struggle to read content if text and background colors have insufficient contrast.",
                                selector=selector,
                                suggested_fix="Increase color contrast to at least 4.5:1 for normal text or 3:1 for large text.",
                                additional_info={'contrast_ratio': ratio, 'foreground': fg, 'background': bg})
                        # 1.4.6 Contrast (Enhanced), AAA: passes AA (>=4.5) but is below 7:1.
                        if self.level >= WCAGLevel.AAA and 4.5 <= ratio < 7.0:
                            self._add_issue("1.4.6", IssueSeverity.SERIOUS,
                                f"Enhanced contrast not met: text '{fg}' on '{bg}' is {ratio:.2f}:1 "
                                f"(Level AAA requires 7:1 for normal text) in CSS rule '{selector}'.",
                                "Level AAA (1.4.6) requires a 7:1 ratio (4.5:1 for large text) so people with moderately low vision can read without assistive technology.",
                                selector=selector,
                                suggested_fix="Increase contrast to at least 7:1 for normal text (4.5:1 for large text) to meet AAA.",
                                additional_info={'contrast_ratio': ratio, 'foreground': fg, 'background': bg})
                    except Exception as e:
                        logger.debug(f"Could not calculate contrast ratio for {fg} and {bg}: {e}")

            # Background images that could affect readability
            if prop.name == 'background-image' and prop.value.strip() != 'none':
                self._add_issue("1.4.5", IssueSeverity.INFO,
                    f"Background image defined in CSS Rule '{selector}'.",
                    "A complex or low-contrast background image can make superimposed text difficult to read.",
                    selector=selector,
                    suggested_fix="Ensure text placed over background images has sufficient contrast.")

        # 2.4.7 Focus Visible: outline: none;
        if self.level >= WCAGLevel.AA:
            if prop.name == 'outline' and prop.value.strip().lower() in ['none', '0', '0px']:
                if prop.priority == 'important':
                    self._add_issue("2.4.7", IssueSeverity.SERIOUS,
                        f"`outline: none !important;` found for rule '{selector}'.",
                        "Suppressing the focus outline with `!important` makes it very difficult for keyboard users to see where focus is.",
                        selector=selector, fix_type=FixType.SEMI_AUTOMATIC,
                        suggested_fix="Remove `outline: none;` or ensure a robust, highly visible alternative focus indicator is provided.",
                        additional_info={'css_property': prop.name, 'css_value': prop.value, 'has_important': True})
                else:
                    self._add_issue("2.4.7", IssueSeverity.MODERATE,
                        f"`outline: none;` found for rule '{selector}'.",
                        "Suppressing the default focus outline makes it difficult for keyboard users to see where focus is.",
                        selector=selector, fix_type=FixType.SEMI_AUTOMATIC,
                        suggested_fix="Provide a visible alternative focus indicator (e.g., `outline` with `color`, `style`, `width`, or `box-shadow`).",
                        additional_info={'css_property': prop.name, 'css_value': prop.value, 'has_important': False})

        # 1.4.12 Text Spacing: restricting user override
        if self.level >= WCAGLevel.AA:
            if prop.name in ['line-height', 'letter-spacing', 'word-spacing', 'text-indent'] and prop.priority == 'important':
                self._add_issue("1.4.12", IssueSeverity.MODERATE,
                    f"Text spacing property `{prop.name}: {prop.value} !important;` found for '{selector}'.",
                    "Using `!important` with text spacing properties can prevent users from overriding styles to improve readability.",
                    selector=selector, suggested_fix="Avoid using `!important` with text spacing properties to allow user stylesheets to function.")

        # Hiding content problematically
        if prop.name in ['display', 'visibility', 'opacity']:
            prop_value_lower = prop.value.strip().lower()
            if (prop.name == 'display' and prop_value_lower == 'none') or \
               (prop.name == 'visibility' and prop_value_lower == 'hidden') or \
               (prop.name == 'opacity' and prop_value_lower == '0'):

                interactive_selector_patterns = [r'\b(a|button|input|select|textarea)\b', r'\[tabindex\]', r':focus', r':hover']
                if any(re.search(pattern, selector, re.IGNORECASE) for pattern in interactive_selector_patterns):
                    self._add_issue("CSS_ANALYSIS", IssueSeverity.MINOR,
                        f"Potentially hidden interactive element: `{prop.name}: {prop.value}` for CSS rule '{selector}'.",
                        "Interactive elements hidden with CSS might not be accessible to all users if they are not exposed programmatically.",
                        selector=selector,
                        suggested_fix="Ensure dynamically hidden interactive elements are correctly announced and operable when visible.",
                        additional_info={'css_property': prop.name, 'css_value': prop.value})


# ==============================================================================
# DYNAMIC ANALYZER
# ==============================================================================

class DynamicAnalyzer(BaseAnalyzer):
    """Performs dynamic analysis using a headless browser (Playwright)."""

    def __init__(self, level: WCAGLevel):
        super().__init__(level)
        self.page: Optional[Page] = None
        self.html_content_full: Optional[str] = None
        self.screenshot_dir: Optional[Path] = None

    async def analyze(self, page: Page, html_content: str, screenshot_dir: Path) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        """Analyze page dynamically using Playwright."""
        self.issues, self.passed = [], []
        self.page = page
        self.html_content_full = html_content
        self.url = page.url
        self.screenshot_dir = screenshot_dir
        self.current_soup = BeautifulSoup(html_content, 'lxml')

        # Run all implemented checks
        for method_name in dir(self):
            if method_name.startswith('_check_'):
                check_method = getattr(self, method_name)
                if inspect.ismethod(check_method) and check_method.__qualname__.startswith(self.__class__.__name__):
                    try:
                        doc = inspect.getdoc(check_method)
                        if doc:
                            match = re.search(r"Criterion (\d[\.\d]+) \(Level (A+)\)", doc)
                            if match:
                                criterion_level = WCAGLevel(match.group(2))
                                if criterion_level <= self.level:
                                    await check_method()
                            else:
                                logger.debug(f"Skipping dynamic check {method_name}: No WCAG criterion found in docstring.")
                        else:
                            logger.debug(f"Skipping dynamic check {method_name}: No docstring found.")
                    except PlaywrightError as pe:
                        logger.debug(f"Playwright error during dynamic check {method_name}: {pe}")
                    except Exception as e:
                        logger.error(f"Error running dynamic check {method_name}: {e}", exc_info=True)

        return self.issues, self.passed

    async def _capture_element_screenshot(self, selector: str) -> Optional[str]:
        """Captures a screenshot of an element and returns its relative path."""
        if not self.screenshot_dir or not self.page:
            return None

        try:
            element_locator = self.page.locator(selector).first
            if await element_locator.count() == 0 or not await element_locator.is_visible():
                logger.debug(f"Skipping screenshot for {selector}: element not found or not visible.")
                return None

            try:
                await element_locator.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(50)
            except PlaywrightError as scroll_err:
                logger.debug(f"Playwright error scrolling {selector} into view: {scroll_err}")

            screenshot_filename = f"dyn_issue_{_safe_filename(selector, 60)}_{secrets.randbelow(9000) + 1000}.png"
            screenshot_full_path = self.screenshot_dir / screenshot_filename

            await element_locator.screenshot(path=str(screenshot_full_path))

            # Compress the image
            try:
                from PIL import Image as PIL_Image
                with PIL_Image.open(screenshot_full_path) as img:
                    img.save(screenshot_full_path, optimize=True, quality=80)
            except Exception as compress_err:
                logger.debug(f"Failed to compress screenshot {screenshot_full_path}: {compress_err}")

            return screenshot_filename
        except PlaywrightError as pe:
            logger.debug(f"Playwright error taking screenshot of element '{selector}': {pe}")
        except Exception as e:
            logger.debug(f"Unexpected error taking screenshot of element '{selector}': {e}")
        return None

    async def _check_1_4_10_reflow(self):
        """Criterion 1.4.10 (Level AA): Checks for horizontal scrolling at small viewports."""
        original_viewport = self.page.viewport_size
        if not original_viewport:
            original_viewport = {"width": 1280, "height": 720}

        try:
            await self.page.set_viewport_size({"width": 320, "height": 1080})
            await self.page.wait_for_timeout(500)

            scroll_width = await self.page.evaluate("document.documentElement.scrollWidth")
            client_width = await self.page.evaluate("document.documentElement.clientWidth")

            if scroll_width > client_width + 10:
                screenshot_filename = await self._capture_element_screenshot('body')
                self._add_issue("1.4.10", IssueSeverity.SERIOUS,
                    f"Horizontal scrolling detected at 320px viewport width (Content Width: {scroll_width}px, Viewport Width: {client_width}px).",
                    "Users with low vision who magnify the screen will have to scroll in two dimensions.",
                    selector="html",
                    suggested_fix="Use responsive design (fluid layouts, media queries, flexbox/grid) to ensure content reflows into a single column.",
                    screenshot_filename=screenshot_filename,
                    additional_info={'actual_width': scroll_width, 'viewport_width': client_width})
            else:
                self._add_passed("1.4.10", "Content reflows correctly without significant horizontal scrolling at 320px width.", elements_checked=1)
        except PlaywrightError as pe:
            logger.debug(f"Playwright error checking reflow for {self.page.url}: {pe}")
        except Exception as e:
            logger.error(f"Error checking reflow for {self.page.url}: {e}", exc_info=True)
        finally:
            if original_viewport:
                await self.page.set_viewport_size(original_viewport)

    async def _check_2_1_1_keyboard_navigation(self):
        """Criterion 2.1.1 (Level A): Checks that all interactive elements are keyboard operable."""
        focusable_elements_query = 'a[href]:not([tabindex="-1"]), button:not([tabindex="-1"]), input:not([type="hidden"]):not([tabindex="-1"]), textarea:not([tabindex="-1"]), select:not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])'

        all_focusable_elements_outerhtml = await self.page.evaluate(
            f'Array.from(document.querySelectorAll("{focusable_elements_query}")).map(el => el.outerHTML)'
        )

        unique_elements_html_set = set(all_focusable_elements_outerhtml)

        if not unique_elements_html_set:
            self._add_passed("2.1.1", "No focusable elements found to test keyboard navigation.", elements_checked=0)
            return

        elements_to_test_count = len(unique_elements_html_set)
        all_focusable_elements_handles = await self.page.query_selector_all(focusable_elements_query)
        handle_map = {await h.evaluate("el => el.outerHTML"): h for h in all_focusable_elements_handles}

        try:
            await self.page.focus('body')
            await self.page.wait_for_timeout(100)

            visited_unique_elements_html = set()
            max_tab_presses = elements_to_test_count * 3 + 5

            for i in range(max_tab_presses):
                await self.page.keyboard.press('Tab')
                await self.page.wait_for_timeout(50)

                current_focused_element_handle = await self.page.evaluate_handle("document.activeElement")
                if current_focused_element_handle:
                    current_tag_name = await current_focused_element_handle.evaluate("el => el.tagName")
                    current_outer_html = await current_focused_element_handle.evaluate("el => el.outerHTML")

                    if (current_tag_name.lower() == 'body' or current_tag_name.lower() == 'html') and i > 0 and len(visited_unique_elements_html) < elements_to_test_count:
                        self._add_issue("2.1.2", IssueSeverity.CRITICAL,
                            "Keyboard focus unexpectedly returned to `<body>` or `<html>` prematurely, indicating possible keyboard trap.",
                            "Keyboard users may become unable to reach all interactive elements or exit a specific component.",
                            selector="body", suggested_fix="Ensure all interactive elements are reachable by keyboard, and that focus can exit all components.")
                        break

                    if current_outer_html in unique_elements_html_set:
                        visited_unique_elements_html.add(current_outer_html)

                    if len(visited_unique_elements_html) == elements_to_test_count:
                        logger.debug("All unique focusable elements visited. Ending tabbing sequence.")
                        break

                else:
                    self._add_issue("2.1.1", IssueSeverity.CRITICAL,
                        "Keyboard focus lost to the browser chrome or unfocusable area during tabbing.",
                        "Keyboard users may become disoriented or unable to navigate without clear focus indication.",
                        selector="document", suggested_fix="Ensure focus always remains on a valid, visible element within the page.")
                    break

            unvisited_elements_html = unique_elements_html_set - visited_unique_elements_html
            if unvisited_elements_html:
                for el_html in list(unvisited_elements_html)[:5]:
                    element_handle_to_screenshot = handle_map.get(el_html)
                    screenshot_filename = None
                    element_selector = "Unknown"
                    if element_handle_to_screenshot:
                        try:
                            element_selector = await element_handle_to_screenshot.evaluate("el => el.tagName + (el.id ? '#' + el.id : '') + (el.className ? '.' + el.className.split(' ')[0] : '')")
                            screenshot_filename = await self._capture_element_screenshot(element_selector)
                        except Exception:
                            element_selector = f"Element HTML: {el_html[:50]}..."
                            logger.debug(f"Failed to capture screenshot or precise selector for unvisited element starting with: {el_html[:50]}...")

                    self._add_issue("2.1.1", IssueSeverity.CRITICAL,
                        f"Interactive element is not reachable by keyboard: {el_html[:100]}",
                        "Users who rely on keyboard navigation cannot access this element, making it unusable for them.",
                        element=el_html, selector=element_selector,
                        suggested_fix="Ensure all interactive elements are reachable via `Tab` key. Check `tabindex` values, CSS properties, or JavaScript that may remove elements from the natural tab order.",
                        screenshot_filename=screenshot_filename)

            if not unvisited_elements_html:
                self._add_passed("2.1.1", "All detected interactive elements appear to be keyboard operable.", elements_checked=elements_to_test_count)

        except PlaywrightError as pe:
            logger.debug(f"Playwright error during keyboard navigation check for {self.page.url}: {pe}")
        except Exception as e:
            logger.error(f"Error during keyboard navigation check for {self.page.url}: {e}", exc_info=True)
        finally:
            await self.page.focus('body')

    async def _check_2_4_7_focus_visible(self):
        """Criterion 2.4.7 (Level AA): Checks if focus indicators are visible and sufficiently contrasted."""
        focusable_elements_query = "a[href]:not([tabindex='-1']), button:not([tabindex='-1']), input:not([type='hidden']):not([tabindex='-1']), textarea:not([tabindex='-1']), select:not([tabindex='-1']), [tabindex]:not([tabindex='-1'])"
        elements = await self.page.query_selector_all(focusable_elements_query)

        checked_elements_count = 0
        for el in elements:
            try:
                bbox = await el.bounding_box()
                if not await el.is_visible() or not bbox or bbox['width'] < 5 or bbox['height'] < 5:
                    continue

                await el.focus()
                await self.page.wait_for_timeout(100)

                focus_info = await el.evaluate('''el => {
                    const style = window.getComputedStyle(el);
                    return {
                        outlineStyle: style.outlineStyle,
                        outlineWidth: parseInt(style.outlineWidth),
                        boxShadow: style.boxShadow,
                        borderWidthSum: parseInt(style.borderTopWidth) + parseInt(style.borderBottomWidth) + parseInt(style.borderLeftWidth) + parseInt(style.borderRightWidth),
                        outerHTML: el.outerHTML.substring(0, 100),
                        selector: el.tagName + (el.id ? '#' + el.id : '') + (el.className ? '.' + el.className.split(' ')[0] : '')
                    };
                }''')

                has_outline = not (focus_info['outlineStyle'] == 'none' or focus_info['outlineWidth'] == 0)
                has_box_shadow = not (focus_info['boxShadow'] == 'none' or focus_info['boxShadow'].strip() == '')

                if not has_outline and not has_box_shadow and focus_info['borderWidthSum'] < 2:
                    element_desc = focus_info['outerHTML']
                    selector = focus_info['selector']
                    screenshot_filename = await self._capture_element_screenshot(selector)
                    self._add_issue("2.4.7", IssueSeverity.SERIOUS,
                        f"Focus indicator may not be sufficiently visible for element: {element_desc}",
                        "Keyboard users cannot visually determine which element currently has focus.",
                        element=element_desc, selector=selector,
                        suggested_fix="Ensure focused elements have a highly visible outline, box-shadow, or other distinct style change.",
                        screenshot_filename=screenshot_filename)
                # Count ALL elements that were actually checked, not just failures
                checked_elements_count += 1
            except PlaywrightError as pe:
                logger.debug(f"Playwright error checking focus visibility for an element: {pe}")
            except Exception as e:
                logger.debug(f"Error checking focus visibility for an element: {e}", exc_info=True)

        if checked_elements_count > 0:
            self._add_passed("2.4.7", "Checked interactive elements for visible focus indicators.", elements_checked=checked_elements_count)
        else:
            self._add_passed("2.4.7", "No focusable elements found to test for focus visibility.", elements_checked=0)

    async def _check_2_5_8_target_size(self):
        """Criterion 2.5.8 (Level AA): Checks interactive targets are at least 24x24 CSS px (with inline/spacing exceptions)."""
        data = await self.page.evaluate("""
            () => {
                const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=menuitem]';
                const els = Array.from(document.querySelectorAll(sel));
                const MAX = 600;
                const sliced = els.slice(0, MAX);
                const rects = sliced.map(e => e.getBoundingClientRect());
                const out = [];
                for (let i = 0; i < sliced.length; i++) {
                    const e = sliced[i], r = rects[i];
                    if (r.width === 0 || r.height === 0) continue;
                    const s = getComputedStyle(e);
                    if (s.visibility === 'hidden' || s.display === 'none') continue;
                    const w = Math.round(r.width), h = Math.round(r.height);
                    if (w >= 24 && h >= 24) continue;
                    const tag = e.tagName.toLowerCase();
                    const inlineLink = tag === 'a' && s.display.indexOf('inline') === 0;
                    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                    let crowded = false;
                    for (let j = 0; j < rects.length; j++) {
                        if (j === i) continue;
                        const o = rects[j];
                        if (o.width === 0 || o.height === 0) continue;
                        const ox = Math.max(o.left, Math.min(cx, o.right));
                        const oy = Math.max(o.top, Math.min(cy, o.bottom));
                        const dx = cx - ox, dy = cy - oy;
                        if (dx * dx + dy * dy < 24 * 24) { crowded = true; break; }
                    }
                    out.push({ w, h, inlineLink, crowded,
                               text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 40),
                               html: (e.outerHTML || '').slice(0, 200) });
                }
                return { total: els.length, measured: sliced.length, capped: els.length > MAX, items: out };
            }
        """)
        flagged = 0
        for it in data.get('items', []):
            # Exceptions: inline link in text, or sufficient spacing (no neighbor within 24px)
            if it['inlineLink'] or not it['crowded']:
                continue
            flagged += 1
            self._add_issue("2.5.8", IssueSeverity.MODERATE,
                f"Interactive target is only {it['w']}x{it['h']}px (below the 24x24 minimum) and is closely spaced to other targets.",
                "Targets smaller than 24x24 CSS pixels without adequate spacing are difficult to activate for users with limited dexterity or who use touch/imprecise pointers.",
                element_html=it['html'],
                suggested_fix="Increase the target to at least 24x24 CSS px, or add spacing so a 24px-diameter circle over the target does not overlap neighboring targets.",
                additional_info={'width_px': it['w'], 'height_px': it['h'], 'target_text': it['text']})
        if data.get('capped'):
            logger.info(f"2.5.8 target-size: measured first {data.get('measured')} of {data.get('total')} targets (cap reached).")
        self._add_passed("2.5.8", f"Checked interactive target sizes ({flagged} undersized & crowded).", elements_checked=data.get('measured', 0))

    async def _check_1_4_11_non_text_contrast(self):
        """Criterion 1.4.11 (Level AA): Checks form-control boundaries are distinguishable from the adjacent background."""
        data = await self.page.evaluate("""
            () => {
                const els = Array.from(document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea'));
                const MAX = 300;
                const out = [];
                for (const e of els.slice(0, MAX)) {
                    const r = e.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const s = getComputedStyle(e);
                    if (s.display === 'none' || s.visibility === 'hidden') continue;
                    let bg = 'rgb(255, 255, 255)', p = e.parentElement;
                    while (p) {
                        const ps = getComputedStyle(p);
                        const c = ps.backgroundColor;
                        if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') { bg = c; break; }
                        p = p.parentElement;
                    }
                    out.push({
                        border: s.borderTopColor,
                        borderVisible: (parseFloat(s.borderTopWidth) || 0) > 0,
                        ctrlBg: s.backgroundColor,
                        pageBg: bg,
                        hasShadow: !!(s.boxShadow && s.boxShadow !== 'none'),
                        html: (e.outerHTML || '').slice(0, 200)
                    });
                }
                return { measured: Math.min(els.length, MAX), capped: els.length > MAX, items: out };
            }
        """)
        flagged = 0
        for it in data.get('items', []):
            if it['hasShadow']:
                continue  # a shadow provides a non-color boundary
            if it['borderVisible']:
                ratio = ColorUtils.get_contrast_ratio(it['border'], it['pageBg'])
                reason = "border"
            else:
                ratio = ColorUtils.get_contrast_ratio(it['ctrlBg'], it['pageBg'])
                reason = "fill (no visible border)"
            if ratio < 3.0:
                flagged += 1
                self._add_issue("1.4.11", IssueSeverity.SERIOUS,
                    f"Form control {reason} has only {ratio:.2f}:1 contrast against the adjacent background (needs >= 3:1).",
                    "Users with low vision may be unable to perceive the boundaries of input fields when the control does not contrast sufficiently with its surroundings.",
                    element_html=it['html'],
                    suggested_fix="Give the control a visible boundary (border, fill, or shadow) with at least 3:1 contrast against the adjacent background.",
                    additional_info={'contrast_ratio': round(ratio, 2)})
        self._add_passed("1.4.11", f"Checked non-text contrast of form-control boundaries ({flagged} insufficient).", elements_checked=data.get('measured', 0))

    async def _check_1_4_1_use_of_color(self):
        """Criterion 1.4.1 (Level A): Flags in-text links distinguished from surrounding text by color alone."""
        data = await self.page.evaluate("""
            () => {
                const links = Array.from(document.querySelectorAll('p a[href], li a[href], td a[href], span a[href], dd a[href]'));
                const MAX = 400;
                const out = [];
                for (const a of links.slice(0, MAX)) {
                    const r = a.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const s = getComputedStyle(a);
                    const td = (s.textDecorationLine || s.textDecoration || '');
                    if (td.indexOf('underline') !== -1) continue;
                    if ((parseFloat(s.borderBottomWidth) || 0) > 0) continue;
                    if (a.querySelector('img, svg')) continue;
                    const parent = a.parentElement;
                    const ps = parent ? getComputedStyle(parent) : null;
                    const bolder = ps ? ((parseInt(s.fontWeight) || 400) >= (parseInt(ps.fontWeight) || 400) + 200) : false;
                    if (bolder) continue;
                    const txt = (a.innerText || '').trim();
                    if (!txt) continue;
                    const pColor = ps ? ps.color : '';
                    if (s.color === pColor) continue;
                    out.push({ text: txt.slice(0, 40), color: s.color, parentColor: pColor, html: (a.outerHTML || '').slice(0, 160) });
                }
                return { measured: Math.min(links.length, MAX), capped: links.length > MAX, items: out };
            }
        """)
        flagged = 0
        for it in data.get('items', []):
            if not it['parentColor']:
                continue
            ratio = ColorUtils.get_contrast_ratio(it['color'], it['parentColor'])
            # G183: color-only differentiation is acceptable only with >=3:1 vs surrounding text (plus a hover/focus cue).
            if ratio < 3.0:
                flagged += 1
                if flagged <= 8:
                    self._add_issue("1.4.1", IssueSeverity.MODERATE,
                        f"In-text link '{it['text']}' appears distinguished from surrounding text by color alone (only {ratio:.2f}:1 vs text color).",
                        "Users who cannot perceive color differences (color blindness, low vision) cannot tell the link from regular text when color is the only cue.",
                        element_html=it['html'],
                        suggested_fix="Add a non-color cue such as an underline, or ensure >= 3:1 contrast against surrounding text plus a distinct hover/focus indicator.",
                        additional_info={'contrast_vs_text': round(ratio, 2)})
        if flagged > 8:
            logger.info(f"1.4.1 use-of-color: {flagged} color-only links found; reported first 8.")
        self._add_passed("1.4.1", f"Checked in-text links for color-only differentiation ({flagged} flagged).", elements_checked=data.get('measured', 0))

    async def _check_2_4_11_focus_not_obscured(self):
        """Criterion 2.4.11 (Level AA): Checks focused elements are not substantially hidden by sticky/fixed overlays."""
        try:
            sticky = await self.page.evaluate("""
                () => {
                    const out = [];
                    for (const e of Array.from(document.querySelectorAll('body *'))) {
                        const s = getComputedStyle(e);
                        if (s.position === 'fixed' || s.position === 'sticky') {
                            const r = e.getBoundingClientRect();
                            if (r.width > 40 && r.height > 10 && s.visibility !== 'hidden' && s.display !== 'none' && (parseFloat(s.opacity) || 1) > 0.1) {
                                out.push({ top: r.top, left: r.left, right: r.right, bottom: r.bottom });
                            }
                        }
                    }
                    return out;
                }
            """)
        except Exception:
            sticky = []

        if not sticky:
            self._add_passed("2.4.11", "No sticky/fixed overlays detected that could obscure focus.", elements_checked=0)
            return

        focusables = await self.page.query_selector_all('a[href], button, input:not([type=hidden]), select, textarea, [tabindex]')
        checked = 0
        flagged = 0
        for el in focusables[:25]:
            try:
                await el.scroll_into_view_if_needed(timeout=1000)
                await el.focus(timeout=1000)
                box = await el.bounding_box()
                if not box:
                    continue
                checked += 1
                area = box['width'] * box['height']
                if area <= 0:
                    continue
                for s in sticky:
                    ox = max(box['x'], s['left'])
                    oy = max(box['y'], s['top'])
                    ox2 = min(box['x'] + box['width'], s['right'])
                    oy2 = min(box['y'] + box['height'], s['bottom'])
                    if ox2 > ox and oy2 > oy and ((ox2 - ox) * (oy2 - oy)) / area >= 0.5:
                        flagged += 1
                        if flagged <= 5:
                            html = await el.evaluate("e => (e.outerHTML || '').slice(0, 200)")
                            self._add_issue("2.4.11", IssueSeverity.SERIOUS,
                                "A focused element is at least half-covered by a sticky/fixed overlay when it receives focus.",
                                "Keyboard users cannot see which component currently has focus when it is hidden behind a sticky header/footer or other fixed overlay.",
                                element_html=html,
                                suggested_fix="Add `scroll-margin`/scroll-padding to offset focused targets, or reduce sticky overlay size, so focused elements remain visible.")
                        break
            except PlaywrightError:
                continue
        if flagged == 0:
            self._add_passed("2.4.11", "Focused elements were not obscured by detected sticky/fixed overlays.", elements_checked=checked)

    async def _check_2_5_5_target_size_enhanced(self):
        """Criterion 2.5.5 (Level AAA): Checks interactive targets are at least 44x44 CSS px."""
        data = await self.page.evaluate("""
            () => {
                const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=menuitem]';
                const els = Array.from(document.querySelectorAll(sel));
                const out = [];
                for (const e of els.slice(0, 600)) {
                    const r = e.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const s = getComputedStyle(e);
                    if (s.visibility === 'hidden' || s.display === 'none') continue;
                    const w = Math.round(r.width), h = Math.round(r.height);
                    if (w >= 44 && h >= 44) continue;
                    if (e.tagName.toLowerCase() === 'a' && s.display.indexOf('inline') === 0) continue;  // inline-link exception
                    out.push({ w, h, text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 40), html: (e.outerHTML || '').slice(0, 200) });
                }
                return { measured: Math.min(els.length, 600), items: out };
            }
        """)
        for it in data.get('items', []):
            self._add_issue("2.5.5", IssueSeverity.MODERATE,
                f"Interactive target is only {it['w']}x{it['h']}px (Level AAA 2.5.5 requires at least 44x44).",
                "Larger 44x44 CSS-pixel targets help users with motor impairments or imprecise pointers activate controls reliably.",
                element_html=it['html'],
                suggested_fix="Increase the target to at least 44x44 CSS pixels (padding counts toward the target).",
                additional_info={'width_px': it['w'], 'height_px': it['h'], 'target_text': it['text']})
        self._add_passed("2.5.5", "Checked interactive target sizes against the 44x44 AAA minimum.", elements_checked=data.get('measured', 0))

    async def _check_2_4_12_focus_not_obscured_enhanced(self):
        """Criterion 2.4.12 (Level AAA): Checks no part of a focused element is hidden by sticky/fixed overlays."""
        try:
            sticky = await self.page.evaluate("""
                () => {
                    const out = [];
                    for (const e of Array.from(document.querySelectorAll('body *'))) {
                        const s = getComputedStyle(e);
                        if (s.position === 'fixed' || s.position === 'sticky') {
                            const r = e.getBoundingClientRect();
                            if (r.width > 40 && r.height > 10 && s.visibility !== 'hidden' && s.display !== 'none' && (parseFloat(s.opacity) || 1) > 0.1) {
                                out.push({ top: r.top, left: r.left, right: r.right, bottom: r.bottom });
                            }
                        }
                    }
                    return out;
                }
            """)
        except Exception:
            sticky = []
        if not sticky:
            self._add_passed("2.4.12", "No sticky/fixed overlays detected that could obscure focus.", elements_checked=0)
            return
        focusables = await self.page.query_selector_all('a[href], button, input:not([type=hidden]), select, textarea, [tabindex]')
        checked = 0
        flagged = 0
        for el in focusables[:25]:
            try:
                await el.scroll_into_view_if_needed(timeout=1000)
                await el.focus(timeout=1000)
                box = await el.bounding_box()
                if not box or box['width'] * box['height'] <= 0:
                    continue
                checked += 1
                for s in sticky:
                    ox = max(box['x'], s['left'])
                    oy = max(box['y'], s['top'])
                    ox2 = min(box['x'] + box['width'], s['right'])
                    oy2 = min(box['y'] + box['height'], s['bottom'])
                    if ox2 > ox and oy2 > oy:  # ANY overlap fails the enhanced criterion
                        flagged += 1
                        if flagged <= 5:
                            html = await el.evaluate("e => (e.outerHTML || '').slice(0, 200)")
                            self._add_issue("2.4.12", IssueSeverity.MODERATE,
                                "Part of a focused element is covered by a sticky/fixed overlay (Level AAA requires none of it be hidden).",
                                "Level AAA (2.4.12) requires the focused component to be fully visible, with no part obscured by other content.",
                                element_html=html,
                                suggested_fix="Add scroll-margin/scroll-padding offsets or shrink sticky overlays so focused elements are never covered.")
                        break
            except PlaywrightError:
                continue
        if flagged == 0:
            self._add_passed("2.4.12", "Focused elements were fully visible (not obscured) on this page.", elements_checked=checked)


# ==============================================================================
# CONTENT ANALYZER
# ==============================================================================

class ContentAnalyzer(BaseAnalyzer):
    """Performs NLP-based analysis on page text content."""

    def __init__(self, level: WCAGLevel):
        super().__init__(level)

    def analyze(self, soup: BeautifulSoup, url_or_path: str) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        """Analyze page content for readability and language issues."""
        self.issues, self.passed = [], []
        self.file_path = url_or_path if not url_or_path.startswith('http') else None
        self.url = url_or_path if url_or_path.startswith('http') else None
        self.current_soup = soup

        if not NLTK_READY:
            self._add_passed("INFO_CONTENT", "Content analysis skipped (NLTK data not available).", url=url_or_path)
            return self.issues, self.passed

        # Extract meaningful text from main content areas
        content_containers = soup.find_all(['main', 'article', 'section'])
        if not content_containers:
            content_containers = soup.find_all('body')

        if not content_containers:
            self._add_passed("INFO_CONTENT", "Content analysis skipped (No main content containers found).", url=url_or_path)
            return self.issues, self.passed

        # Aggregate text from all identified content containers
        full_text_content_list = []
        for container in content_containers:
            temp_container = BeautifulSoup(str(container), 'lxml')
            for element in temp_container(['script', 'style', 'header', 'footer', 'nav', 'aside', 'form', 'svg', 'a', 'button']):
                element.decompose()
            full_text_content_list.append(temp_container.get_text(separator=' ', strip=True))

        text = " ".join(full_text_content_list)

        if not text or len(text) < 200:
            self._add_passed("INFO_CONTENT", "Content analysis skipped due to insufficient text length (< 200 characters).", url=url_or_path)
            return self.issues, self.passed

        # Criterion 3.1.5 (Reading Level) - Level AAA
        if WCAGLevel.AAA <= self.level:
            try:
                grade_level = flesch_kincaid_grade(text)
                if grade_level > 9:
                    self._add_issue(
                        criterion="3.1.5", severity=IssueSeverity.MODERATE,
                        description=f"The page content has a high reading grade level (Flesch-Kincaid: {grade_level:.1f}).",
                        impact="Users with cognitive or reading disabilities may struggle to understand complex content.",
                        suggested_fix="Simplify sentence structure and vocabulary to aim for a reading level of a lower secondary education (around grade 9 or lower).",
                        additional_info={"flesch_kincaid_grade_level": grade_level}
                    )
                else:
                    self._add_passed("3.1.5", f"Content reading level is appropriate (Flesch-Kincaid: {grade_level:.1f}).")
            except Exception as e:
                logger.debug(f"Could not calculate reading level for {url_or_path}: {e}")
                self._add_passed("3.1.5", "Reading level check failed to run.", details=str(e))

        # Criterion 3.1.3 (Unusual Words) & 3.1.4 (Abbreviations) - Level AAA
        if WCAGLevel.AAA <= self.level:
            words = textstat.get_words(text)

            # Complex words heuristic
            complex_words = [w for w in words if textstat.syllable_count(w) >= 4 and len(w) > 6 \
                             and not (w.isupper() and len(w) > 1) and not w.isdigit()]

            if complex_words:
                self._add_issue(
                    criterion="3.1.3", severity=IssueSeverity.MINOR,
                    description=f"Page contains potentially unusual or complex words/jargon. ({', '.join(list(set(complex_words))[:5])}...)",
                    impact="Users with cognitive disabilities, those with limited vocabulary, or non-native speakers may find it difficult to understand specialized terminology.",
                    suggested_fix="Define jargon, acronyms, and complex words in context, provide definitions via tooltips, or include a glossary.",
                    additional_info={"example_complex_words": list(set(complex_words))[:10]}
                )
            else:
                self._add_passed("3.1.3", "No significantly complex words detected (heuristic).")

            # Basic abbreviation check
            potential_abbreviations = re.findall(r'\b[A-Z]{2,5}\b', text)
            potential_abbreviations = [abbr for abbr in potential_abbreviations if not abbr.startswith(('HTTP', 'HTML', 'CSS', 'JS', 'URL'))]

            common_initials = {"US", "UK", "EU", "UN", "IT", "CEO", "CFO", "CTO", "HR", "PR", "AI", "FYI", "B2B", "SEO", "NASA", "FBI"}
            potential_abbreviations = [abbr for abbr in potential_abbreviations if abbr not in common_initials]

            if potential_abbreviations:
                self._add_issue(
                    criterion="3.1.4", severity=IssueSeverity.MINOR,
                    description=f"Page contains potential abbreviations or acronyms without definitions. (e.g., {', '.join(list(set(potential_abbreviations))[:5])[:75]})",
                    impact="Users may not understand the meaning of abbreviations if they are not explicitly defined.",
                    suggested_fix="Define all abbreviations and acronyms on first use, or use the `<abbr>` tag with a `title` attribute to provide expansions.",
                    additional_info={"example_abbreviations": list(set(potential_abbreviations))[:10]}
                )
            else:
                self._add_passed("3.1.4", "No obvious abbreviations detected (heuristic).")

        return self.issues, self.passed


# ==============================================================================
# HTML VALIDATION + SPELLING (non-WCAG "Standards"/"Errors" checks, à la SortSite)
# ==============================================================================

class HTMLValidationAnalyzer(BaseAnalyzer):
    """Flags common HTML standards/validity defects (SortSite 'Standards'/'Errors' territory)."""

    DEPRECATED_ELEMENTS = {
        'center', 'font', 'strike', 'big', 'tt', 'marquee', 'blink', 'frame', 'frameset',
        'noframes', 'applet', 'basefont', 'dir', 'isindex', 'acronym', 'bgsound', 'nobr',
        'plaintext', 'spacer',
    }
    DEPRECATED_ATTRS = {
        'align', 'bgcolor', 'background', 'border', 'cellpadding', 'cellspacing', 'valign',
        'hspace', 'vspace', 'nowrap', 'clear', 'compact', 'frameborder', 'marginheight',
        'marginwidth', 'scrolling', 'noshade', 'link', 'vlink', 'alink',
    }

    def analyze(self, html_content: str, url_or_path: str,
                soup: Optional[BeautifulSoup] = None) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        self.issues, self.passed = [], []
        if url_or_path.startswith('http'):
            self.url = url_or_path
            self.file_path = None
        else:
            self.file_path = url_or_path
            self.url = None
        self.current_soup = soup or BeautifulSoup(html_content, 'lxml')
        soup = self.current_soup

        # Missing DOCTYPE
        if '<!doctype' not in html_content[:512].lower():
            self._add_issue("HTML_VALIDATION", IssueSeverity.MINOR,
                "Missing <!DOCTYPE html> declaration.",
                "Without a doctype, browsers may use quirks mode, causing inconsistent layout and behavior.",
                suggested_fix="Add `<!DOCTYPE html>` as the very first line of the document.")

        # Missing character encoding
        if not soup.find('meta', charset=True) and not soup.find('meta', attrs={'http-equiv': re.compile('content-type', re.I)}):
            self._add_issue("HTML_VALIDATION", IssueSeverity.MINOR,
                "No character encoding (<meta charset>) declared.",
                "A missing charset can garble text and is a validation error.",
                suggested_fix='Add `<meta charset="utf-8">` early in <head>.')

        # Duplicate IDs
        id_map: Dict[str, list] = {}
        for el in soup.find_all(attrs={'id': True}):
            _id = el.get('id')
            _id = ' '.join(_id) if isinstance(_id, list) else _id
            if _id:
                id_map.setdefault(_id, []).append(el)
        for _id, els in id_map.items():
            if len(els) > 1:
                self._add_issue("HTML_VALIDATION", IssueSeverity.MODERATE,
                    f"Duplicate id '{_id}' used {len(els)} times.",
                    "IDs must be unique; duplicates break label/control associations, in-page anchors, and scripting.",
                    element=els[0], suggested_fix=f"Make each id unique ('{_id}' is repeated).")

        # Deprecated/obsolete elements
        for tag in soup.find_all(list(self.DEPRECATED_ELEMENTS)):
            self._add_issue("HTML_VALIDATION", IssueSeverity.MINOR,
                f"Obsolete/deprecated element <{tag.name}>.",
                "Obsolete elements are invalid in HTML5 and are not guaranteed to be supported.",
                element=tag, suggested_fix=f"Replace <{tag.name}> with a modern element styled via CSS.")

        # Deprecated presentational attributes (capped)
        attr_hits = 0
        for el in soup.find_all(True):
            for attr in list(el.attrs.keys()):
                if attr.lower() in self.DEPRECATED_ATTRS:
                    attr_hits += 1
                    if attr_hits <= 100:
                        self._add_issue("HTML_VALIDATION", IssueSeverity.MINOR,
                            f"Deprecated presentational attribute '{attr}' on <{el.name}>.",
                            "Presentational HTML attributes are obsolete in HTML5; presentation belongs in CSS.",
                            element=el, suggested_fix=f"Remove '{attr}' and move styling to CSS.")

        # <label for> pointing at a non-existent id
        for label in soup.find_all('label', attrs={'for': True}):
            target = label.get('for')
            if target and not soup.find(id=target):
                self._add_issue("HTML_VALIDATION", IssueSeverity.MODERATE,
                    f"<label for='{target}'> references a non-existent id.",
                    "A label whose `for` matches no element provides no programmatic association with a control.",
                    element=label, suggested_fix="Point `for` at an existing control id, or wrap the control inside the label.")

        # Interactive nested inside an anchor (invalid)
        for a in soup.find_all('a'):
            if a.find('a') or a.find('button'):
                self._add_issue("HTML_VALIDATION", IssueSeverity.MODERATE,
                    "Interactive element nested inside an <a> (invalid HTML).",
                    "Anchors must not contain other links/buttons; this is invalid and breaks keyboard/AT behavior.",
                    element=a, suggested_fix="Restructure so interactive controls are siblings, not nested inside the link.")

        self._add_passed("HTML_VALIDATION", "Checked HTML for common standards/validity defects.", elements_checked=len(soup.find_all(True)))
        return self.issues, self.passed


class SpellingAnalyzer(BaseAnalyzer):
    """Spell-checks visible page text (SortSite 'Errors' → Spelling). Needs the optional 'pyspellchecker'."""
    _spell_cache: Dict[str, Any] = {}

    def __init__(self, level: WCAGLevel, lang: str = 'en', ignore_caps: bool = True):
        super().__init__(level)
        self.lang = lang
        self.ignore_caps = ignore_caps

    def analyze(self, soup: BeautifulSoup, url_or_path: str) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        self.issues, self.passed = [], []
        if url_or_path.startswith('http'):
            self.url = url_or_path
            self.file_path = None
        else:
            self.file_path = url_or_path
            self.url = None
        self.current_soup = soup

        if not SPELL_READY:
            self._add_passed("SPELLING", "Spell check skipped (install 'pyspellchecker' to enable).", url=url_or_path)
            return self.issues, self.passed

        spell = SpellingAnalyzer._spell_cache.get(self.lang)
        if spell is None:
            try:
                spell = _SpellChecker(language=self.lang)
                SpellingAnalyzer._spell_cache[self.lang] = spell
            except Exception as e:
                self._add_passed("SPELLING", f"Spell check unavailable for language '{self.lang}': {e}", url=url_or_path)
                return self.issues, self.passed

        # Visible text only (drop code/markup-ish content).
        tmp = BeautifulSoup(str(soup), 'lxml')
        for t in tmp(['script', 'style', 'code', 'pre', 'kbd', 'samp', 'noscript', 'svg']):
            t.decompose()
        text = tmp.get_text(' ', strip=True)
        # Strip URLs / emails so technical tokens aren't flagged as misspellings.
        text = re.sub(r'https?://\S+|www\.\S+|\S+@\S+\.\S+', ' ', text)

        candidates, seen = [], set()
        for w in re.findall(r"[A-Za-z][A-Za-z'’]+", text):
            wl = w.strip("'’")
            if len(wl) < 4 or not wl.isascii():  # skip short tokens and encoding artifacts
                continue
            if self.ignore_caps and (wl[0].isupper() or wl.isupper()):
                continue  # skip likely proper nouns / acronyms
            low = wl.lower()
            if low in seen or low in _SPELL_ALLOWLIST:
                continue
            seen.add(low)
            candidates.append(wl)
            if len(candidates) >= 2000:
                break

        try:
            unknown = spell.unknown([c.lower() for c in candidates])
        except Exception as e:
            self._add_passed("SPELLING", f"Spell check failed to run: {e}", url=url_or_path)
            return self.issues, self.passed

        reported = 0
        for c in candidates:
            if c.lower() in unknown:
                reported += 1
                if reported > 100:
                    break
                try:
                    sugg = spell.correction(c.lower())
                except Exception:
                    sugg = None
                hint = f' (did you mean "{sugg}"?)' if sugg and sugg != c.lower() else ''
                self._add_issue("SPELLING", IssueSeverity.MINOR,
                    f'Possible misspelling: "{c}"{hint}',
                    "Spelling errors reduce clarity and credibility and can confuse users with cognitive or reading disabilities.",
                    suggested_fix=f'Verify the spelling of "{c}".' + (f' Suggested: "{sugg}".' if sugg else ''),
                    additional_info={'word': c, 'suggestion': sugg})
        self._add_passed("SPELLING", f"Spell-checked visible text ({len(candidates)} unique words).", elements_checked=len(candidates))
        return self.issues, self.passed


# ==============================================================================
# CONSISTENCY ANALYZER (cross-page)
# ==============================================================================

class ConsistencyAccumulator:
    """Aggregates lightweight cross-page signals for SC 3.2.3/3.2.4/3.2.6.

    Updated incrementally per page (no per-page storage) and bounded by MAX_KEYS so a
    very large crawl cannot exhaust memory. All updates are synchronous (no awaits), so
    concurrent analysis coroutines can call add() safely under asyncio.
    """
    MAX_KEYS = 20000

    def __init__(self):
        self.page_count = 0
        self.nav_groups: Dict[Any, Dict[str, Any]] = {}
        self.link_texts: Dict[str, Set[str]] = {}
        self.link_pages: Dict[str, int] = {}
        self.img_alts: Dict[str, Set[str]] = {}
        self.help_pages = 0
        self.help_buckets: Dict[str, int] = {}
        self.truncated = False

    def add(self, soup: BeautifulSoup, url: str):
        self.page_count += 1

        # 3.2.3: navigation order
        nav = soup.find('nav') or soup.find(attrs={'role': 'navigation'})
        if nav:
            texts = tuple(t for a in nav.find_all('a', href=True) if (t := a.get_text(strip=True)))
            if len(texts) >= 2:
                key = frozenset(texts)
                grp = self.nav_groups.get(key)
                if grp is None and len(self.nav_groups) < self.MAX_KEYS:
                    grp = self.nav_groups[key] = {'orders': set(), 'examples': []}
                if grp is not None:
                    grp['orders'].add(texts)
                    if len(grp['examples']) < 5:
                        grp['examples'].append(url)

        # 3.2.4: link label consistency per destination
        for a in soup.find_all('a', href=True):
            t = a.get_text(strip=True)
            if not t:
                continue
            href = a['href'].strip()
            if not href or href.startswith('#') or href.lower().startswith('javascript:'):
                continue
            if href in self.link_texts:
                self.link_texts[href].add(t)
                self.link_pages[href] += 1
            elif len(self.link_texts) < self.MAX_KEYS:
                self.link_texts[href] = {t}
                self.link_pages[href] = 1
            else:
                self.truncated = True

        # 3.2.4: alt consistency per image
        for img in soup.find_all('img'):
            if not img.has_attr('alt'):
                continue
            src = (img.get('src') or '').strip()
            alt = img['alt'].strip()
            if not src or not alt:
                continue
            if src in self.img_alts:
                self.img_alts[src].add(alt)
            elif len(self.img_alts) < self.MAX_KEYS:
                self.img_alts[src] = {alt}
            else:
                self.truncated = True

        # 3.2.6: help mechanism location bucket
        for a in soup.find_all('a', href=True):
            href = a['href'].strip().lower()
            txt = (a.get_text(strip=True) or a.get('aria-label') or '').strip().lower()
            if href.startswith(('mailto:', 'tel:')) or any(k in txt for k in ('help', 'contact', 'support', 'faq')):
                if a.find_parent(['header', 'nav']):
                    bucket = 'header/nav'
                elif a.find_parent('footer'):
                    bucket = 'footer'
                else:
                    bucket = 'body'
                self.help_pages += 1
                self.help_buckets[bucket] = self.help_buckets.get(bucket, 0) + 1
                break


class ConsistencyAnalyzer(BaseAnalyzer):
    """Cross-page consistency checks (WCAG 3.2.3, 3.2.4, 3.2.6) over aggregated crawl data."""

    def analyze(self, acc: ConsistencyAccumulator) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        self.issues, self.passed = [], []
        self.url = None
        self.file_path = None
        if acc.page_count < 2:
            return self.issues, self.passed

        # 3.2.3 Consistent Navigation
        nav_issues = 0
        for grp in acc.nav_groups.values():
            if len(grp['orders']) > 1:
                nav_issues += 1
                self._add_issue("3.2.3", IssueSeverity.MODERATE,
                    "Repeated navigation presents its links in a different relative order across pages.",
                    "When navigation that repeats across pages changes order, users (especially screen-reader and keyboard users) lose a predictable, consistent navigation pattern.",
                    suggested_fix="Render shared navigation in the same relative order on every page.",
                    additional_info={'example_pages': grp['examples'], 'orders_seen': [list(o) for o in list(grp['orders'])[:3]]})
        if nav_issues == 0:
            self._add_passed("3.2.3", "Repeated navigation order was consistent across analyzed pages.", elements_checked=len(acc.nav_groups))

        # 3.2.4 Consistent Identification
        ident_issues = 0
        for href, texts in acc.link_texts.items():
            if acc.link_pages.get(href, 0) >= 3 and len({re.sub(r'\s+', ' ', t).strip().lower() for t in texts}) >= 2:
                ident_issues += 1
                if ident_issues <= 10:
                    self._add_issue("3.2.4", IssueSeverity.MODERATE,
                        f"The same destination ({href[:80]}) is labeled with different link text across pages.",
                        "Components with the same function should be identified consistently; differing labels for one destination confuse users who rely on consistent naming.",
                        suggested_fix="Use a single consistent, descriptive label for links pointing to the same destination.",
                        additional_info={'destination': href, 'labels_seen': sorted(texts)[:6]})
        for src, alts in acc.img_alts.items():
            if len({re.sub(r'\s+', ' ', a).strip().lower() for a in alts}) >= 2:
                ident_issues += 1
                if ident_issues <= 10:
                    self._add_issue("3.2.4", IssueSeverity.MINOR,
                        f"The same image ({src[:80]}) uses different alt text across pages.",
                        "Repeated images serving the same function should have consistent text alternatives.",
                        suggested_fix="Use consistent alt text for the same image when it serves the same purpose.",
                        additional_info={'image': src, 'alts_seen': sorted(alts)[:6]})
        if ident_issues == 0:
            self._add_passed("3.2.4", "Repeated links/images were identified consistently across analyzed pages.")

        # 3.2.6 Consistent Help
        if acc.help_pages > 0 and len(acc.help_buckets) > 1:
            self._add_issue("3.2.6", IssueSeverity.MODERATE,
                "A help mechanism appears in different page regions across the site (e.g., header on some pages, footer/body on others).",
                "When present on multiple pages, help mechanisms must occur in the same relative order/location so users can reliably find help.",
                suggested_fix="Place the help mechanism (contact, support, etc.) in a consistent location and relative order across all pages.",
                additional_info={'locations_seen': acc.help_buckets})
        else:
            self._add_passed("3.2.6", "Help mechanism location was consistent (or absent) across analyzed pages.")

        if acc.truncated:
            logger.info("Consistency analysis hit its key cap; results are based on a bounded sample of links/images.")
        return self.issues, self.passed


# ==============================================================================
# AXE ANALYZER
# ==============================================================================

_AXE_VERSION = "4.12.0"
_AXE_CDN_URL = f"https://cdn.jsdelivr.net/npm/axe-core@{_AXE_VERSION}/axe.min.js"
# SHA-256 of the pinned axe.min.js above; fetched bytes are verified before injection (F3 / A08).
_AXE_SHA256 = "a0afc408abecbe06ff7675033e7ea2c4046618162167aaa4ce1030b10963dece"
_AXE_SOURCE_CACHE: Optional[str] = None
_AXE_LOCAL_OVERRIDE: Optional[str] = None  # set from --axe-script for offline / air-gapped use


async def _get_axe_source() -> Optional[str]:
    """Return the axe-core source ONCE per run, with integrity verification.

    Order: a local --axe-script file (trusted-local), else the pinned CDN URL whose bytes are
    verified against _AXE_SHA256. On any failure or hash mismatch we fail CLOSED (return None and
    skip the axe pass) rather than inject unverified script into the page under test.
    """
    global _AXE_SOURCE_CACHE
    if _AXE_SOURCE_CACHE is not None:
        return _AXE_SOURCE_CACHE or None

    if _AXE_LOCAL_OVERRIDE:
        try:
            local_path = Path(_AXE_LOCAL_OVERRIDE)
            with local_path.open('rb') as handle:
                raw_local = handle.read(5 * 1024 * 1024 + 1)
            if len(raw_local) > 5 * 1024 * 1024:
                raise ValueError("local axe script exceeds the 5 MiB safety limit")
            src = raw_local.decode('utf-8')
            _AXE_SOURCE_CACHE = src
            logger.info(f"axe-core loaded from local file '{_AXE_LOCAL_OVERRIDE}' "
                        f"(sha256={hashlib.sha256(raw_local).hexdigest()[:12]}).")
            return src
        except Exception as e:
            logger.error(f"Could not read --axe-script '{_AXE_LOCAL_OVERRIDE}': {e}; skipping axe.")
            _AXE_SOURCE_CACHE = ""
            return None

    try:
        async with aiohttp.ClientSession(connector=_safe_connector(False)) as session:
            async with _safe_get(
                session, _AXE_CDN_URL, allow_private=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    raw_source = await _read_capped_bytes(resp, 5 * 1024 * 1024)
                    digest = hashlib.sha256(raw_source).hexdigest()
                    if digest != _AXE_SHA256:
                        logger.error(f"axe-core integrity check FAILED (expected {_AXE_SHA256[:12]}, "
                                     f"got {digest[:12]}); refusing to inject unverified script.")
                        _AXE_SOURCE_CACHE = ""
                        return None
                    src = raw_source.decode('utf-8')
                    _AXE_SOURCE_CACHE = src
                    logger.info(f"axe-core {_AXE_VERSION} fetched and integrity-verified.")
                    return src
                logger.debug(f"axe-core fetch returned HTTP {resp.status}.")
    except Exception as e:
        logger.debug(f"Could not fetch axe-core ({e}).")
    _AXE_SOURCE_CACHE = ""
    return None


def _wcag_ids_from_axe_tags(tags: List[str]) -> List[str]:
    """Map axe-core 'wcagNNN' tags to dotted WCAG success-criterion ids.

    Axe emits success criteria as digit-runs (no dots/colons), e.g. 'wcag111' -> 1.1.1,
    'wcag1410' -> 1.4.10, 'wcag258' -> 2.5.8. Version/level tags like 'wcag2aa'/'wcag21aa'
    carry trailing letters and are NOT criteria, so they are ignored.
    """
    ids: List[str] = []
    for tag in tags:
        m = re.fullmatch(r'wcag(\d{3,4})', tag.strip().lower())
        if m:
            d = m.group(1)
            ids.append(f"{d[0]}.{d[1]}.{d[2:]}")
    return ids


# Axe "best-practice" rules carry no wcagNNN tag; map the common ones to the SC they support
# so they're attributed properly instead of showing as "Unknown".
AXE_RULE_TO_SC = {
    "heading-order": "1.3.1", "empty-heading": "1.3.1", "page-has-heading-one": "1.3.1",
    "region": "1.3.1", "landmark-unique": "1.3.1", "landmark-one-main": "1.3.1",
    "landmark-no-duplicate-banner": "1.3.1", "landmark-no-duplicate-contentinfo": "1.3.1",
    "landmark-complementary-is-top-level": "1.3.1", "landmark-main-is-top-level": "1.3.1",
    "duplicate-id-aria": "4.1.2", "aria-allowed-role": "4.1.2", "frame-title-unique": "4.1.2",
    "scrollable-region-focusable": "2.1.1", "tabindex": "2.4.3", "bypass": "2.4.1", "skip-link": "2.4.1",
}


class Axe:
    """Simple Axe-core wrapper for Playwright"""

    def __init__(self):
        pass

    async def run_on_context(self, page: Page) -> Dict[str, Any]:
        """Run axe-core analysis on a Playwright page"""
        try:
            # Inject only the integrity-verified source; fail closed (skip axe) otherwise (F3).
            axe_source = await _get_axe_source()
            if not axe_source:
                logger.debug("axe-core unavailable or failed integrity check; skipping axe pass for this page.")
                return {'violations': [], 'passes': []}
            await page.add_script_tag(content=axe_source)

            # Run axe and return results
            results = await page.evaluate("""
                async () => {
                    if (typeof axe !== 'undefined') {
                        return await axe.run();
                    }
                    return {violations: [], passes: []};
                }
            """)
            return results
        except Exception as e:
            logger.debug(f"Axe analysis failed: {e}")
            return {'violations': [], 'passes': []}


class AxeAnalyzer(BaseAnalyzer):
    """Runs the Axe-core engine using Playwright."""

    def __init__(self, level: WCAGLevel):
        super().__init__(level)
        self.axe = Axe()

    async def analyze(self, page: Page, html_content: str, screenshot_path_base: Optional[Path]) -> Tuple[List[AccessibilityIssue], List[PassedCheck]]:
        """Run Axe-core analysis on a live page."""
        self.issues, self.passed = [], []
        self.url = page.url
        self.current_soup = BeautifulSoup(html_content, 'lxml')

        try:
            results = await self.axe.run_on_context(page)

            # Process violations (issues)
            for violation in results['violations']:
                wcag_ids = _wcag_ids_from_axe_tags(violation.get('tags', []))

                if not wcag_ids:
                    mapped = AXE_RULE_TO_SC.get(violation['id'])
                    wcag_ids = [mapped] if mapped else [violation['id']]

                # Filter issues based on configured WCAG level
                is_level_compliant = False
                issue_wcag_id = None
                for wcag_id in wcag_ids:
                    details = get_criterion_details(wcag_id)
                    if details['level'] <= self.level:
                        is_level_compliant = True
                        issue_wcag_id = wcag_id
                        break

                if not is_level_compliant:
                    continue

                # Process each node in the violation
                for node in violation['nodes']:
                    screenshot_fname = None
                    if screenshot_path_base:
                        try:
                            locator_str = node['target'][0]
                            locator = page.locator(locator_str).first
                            if await locator.is_visible() and await locator.bounding_box():
                                screenshot_filename = f"axe_violation_{_safe_filename(violation['id'])}_{len(self.issues)}.png"
                                screenshot_full_path = screenshot_path_base / screenshot_filename

                                await locator.screenshot(path=str(screenshot_full_path))

                                try:
                                    from PIL import Image as PIL_Image
                                    with PIL_Image.open(screenshot_full_path) as img:
                                        img.save(screenshot_full_path, optimize=True, quality=80)
                                except Exception as compress_err:
                                    logger.debug(f"Failed to compress screenshot {screenshot_full_path}: {compress_err}")

                                screenshot_fname = screenshot_filename
                        except PlaywrightError as screenshot_err:
                            logger.debug(f"Playwright error during screenshot for Axe violation: {screenshot_err}")
                        except Exception as screenshot_err:
                            logger.debug(f"Unexpected error when taking screenshot for Axe violation: {screenshot_err}")

                    self._add_issue(
                        criterion=issue_wcag_id,
                        severity=self._map_severity(violation['impact']),
                        description=violation['help'],
                        impact=f"Impact: {violation.get('impact', 'N/A')}. {node.get('failureSummary', '')}",
                        element=node.get('target', [None])[0],
                        element_html=node.get('html'),
                        selector=node.get('target', [None])[0],
                        suggested_fix=violation['helpUrl'],
                        additional_info={'axe_rule_id': violation['id'], 'axe_data_raw': node},
                        screenshot_filename=screenshot_fname
                    )

            # Process passes (passed checks)
            for passed_item in results['passes']:
                wcag_ids_passed = _wcag_ids_from_axe_tags(passed_item.get('tags', []))

                is_level_compliant = False
                passed_wcag_id = None
                for wcag_id in wcag_ids_passed:
                    details = get_criterion_details(wcag_id)
                    if details['level'] <= self.level:
                        is_level_compliant = True
                        passed_wcag_id = wcag_id
                        break

                if not is_level_compliant:
                    continue

                passed_wcag_id = passed_wcag_id if passed_wcag_id else "AXE_PASSED_GENERIC"

                self._add_passed(
                    criterion=passed_wcag_id,
                    description=passed_item['description'],
                    elements_checked=len(passed_item['nodes']),
                    details=f"Axe rule ID: {passed_item['id']}"
                )

        except Exception as e:
            logger.error(f"Axe-core analysis failed for {page.url}: {e}", exc_info=True)

        return self.issues, self.passed

    def _map_severity(self, impact: str) -> IssueSeverity:
        """Map Axe-core impact to our IssueSeverity enum."""
        return {
            'critical': IssueSeverity.CRITICAL,
            'serious': IssueSeverity.SERIOUS,
            'moderate': IssueSeverity.MODERATE,
            'minor': IssueSeverity.MINOR,
            'informational': IssueSeverity.INFO
        }.get(impact, IssueSeverity.INFO)



################################################################################
# BEGIN crawler.py

try:
    from charset_normalizer import from_bytes as _cn_from_bytes
except ImportError:
    _cn_from_bytes = None


logger = logging.getLogger(__name__)


class Crawler:
    """Asynchronously crawls a website to discover pages and assets."""

    def __init__(self, start_url: str, max_depth: Optional[Union[int, float]], concurrency: int,
                 exclude_patterns: List[str], user_agent: str, report: AccessibilityReport,
                 crawl_delay: float, max_urls_to_crawl: Optional[int] = None,
                 scope: str = "site", allow_private_hosts: bool = False):
        self.start_url = start_url
        self.allow_private_hosts = allow_private_hosts
        # max_depth of None or a non-positive value means "no matter the depth".
        self.max_depth = float('inf') if (not max_depth or max_depth <= 0) else max_depth
        self.concurrency = concurrency
        self.crawl_delay = crawl_delay
        # Compile regex patterns for exclusion
        self.exclude_patterns = [re.compile(p, re.IGNORECASE) for p in exclude_patterns]
        self.user_agent = user_agent
        self.report = report
        self.max_urls_to_crawl = max_urls_to_crawl
        # Scan scope: "site" = whole domain; "folder" = restricted to the start URL's directory.
        self.scope = scope

        parsed_start = urlparse(start_url)
        self.base_hostname = (parsed_start.hostname or "").lower().rstrip('.')
        self.base_explicit_port = parsed_start.port
        # For "folder" scope, only crawl URLs under this path prefix (derived from the target).
        self.folder_prefix_path = self._compute_folder_prefix_path(start_url) if scope == "folder" else None
        self.queue = asyncio.Queue()
        # Queued and processed states are deliberately separate. Conflating
        # them caused queued URLs to be rejected immediately before fetching.
        self.seen_urls: Set[str] = set()
        self.processed_urls: Set[str] = set()
        self.robot_parsers: Dict[str, RobotFileParser] = {}
        self._robots_lock = asyncio.Lock()
        self.session: Optional[aiohttp.ClientSession] = None
        self.crawled_count = 0
        self.progress_tasks: List[Any] = []
        self.progress_reporter: Optional[Progress] = None
        # Semaphore for strict concurrency control
        self._limit_concurrent_requests = asyncio.Semaphore(concurrency)

    @staticmethod
    def _compute_folder_prefix_path(start_url: str) -> str:
        """Derive the directory path prefix that constrains a 'folder'-scoped crawl.

        The folder is identified by the target the user provides:
            /docs/guide.html -> /docs/   (a file: use its containing folder)
            /docs/           -> /docs/   (an explicit folder)
            /docs            -> /docs/   (no extension: treat the path as a folder)
            '' or /          -> /        (site root: equivalent to whole-site)
        """
        path = urlparse(start_url).path or "/"
        if path.endswith("/"):
            folder_path = path
        else:
            last_segment = path.rsplit("/", 1)[-1]
            if "." in last_segment:  # looks like a file -> use its containing folder
                folder_path = path.rsplit("/", 1)[0] + "/"
            else:  # no extension -> treat the path itself as a folder
                folder_path = path + "/"
        return folder_path or "/"

    async def _can_fetch(self, url: str) -> bool:
        """Checks if crawling is allowed by robots.txt."""
        parsed_url = urlparse(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"

        if origin not in self.robot_parsers:
            async with self._robots_lock:
                if origin not in self.robot_parsers:
                    rp = RobotFileParser()
                    try:
                        # The caller already owns the request semaphore. Do not
                        # acquire it again because concurrency=1 would deadlock.
                        async with _safe_get(
                            self.session,
                            urljoin(origin, "/robots.txt"),
                            allow_private=self.allow_private_hosts,
                            timeout=10,
                        ) as resp:
                            if resp.status == 200:
                                lines = (await _read_capped_text(resp, 512 * 1024)).splitlines()
                                rp.parse(lines)
                            else:
                                rp.allow_all = True
                                logger.info(
                                    "Could not retrieve robots.txt from %s (status %s); allowing crawl.",
                                    origin,
                                    resp.status,
                                )
                    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                        rp.allow_all = True
                        logger.debug("Error fetching robots.txt from %s: %s", origin, e)
                    except Exception as e:
                        rp.allow_all = True
                        logger.error("Unexpected robots.txt error for %s: %s", origin, e)
                    self.robot_parsers[origin] = rp

        return self.robot_parsers[origin].can_fetch(self.user_agent, url)

    def _is_valid_url(self, url: str, *, check_seen: bool = True) -> bool:
        """Checks if a URL is within scope and not excluded."""
        if not url or len(url) > MAX_URL_LENGTH:
            return False
        # Normalize URL by removing fragment and redundant slashes
        defragged_url, _ = urldefrag(url)
        # Consistent trailing slash removal
        if defragged_url.endswith('/'):
            defragged_url = defragged_url.rstrip('/')
        # Handle index.html or default documents at root for better deduplication
        if defragged_url.endswith('/index.html'):
            defragged_url = defragged_url.replace('/index.html', '')

        parsed = urlparse(defragged_url)
        if parsed.scheme not in ('http', 'https'):
            return False
        if parsed.username or parsed.password:
            return False
        try:
            candidate_port = parsed.port
        except ValueError:
            return False

        # Stay on the same domain or subdomain
        hostname = (parsed.hostname or "").lower().rstrip('.')
        if hostname != self.base_hostname and not hostname.endswith(f".{self.base_hostname}"):
            return False
        if self.base_explicit_port is not None:
            if candidate_port != self.base_explicit_port:
                return False
        elif candidate_port is not None:
            default_port = 443 if parsed.scheme == 'https' else 80
            if candidate_port != default_port:
                return False

        # For "folder" scope, stay within the user-identified directory prefix
        if self.folder_prefix_path:
            candidate_path = parsed.path if parsed.path.endswith('/') else parsed.path + '/'
            if not candidate_path.startswith(self.folder_prefix_path):
                return False

        # Check against user-defined exclusion patterns
        if any(pattern.search(defragged_url) for pattern in self.exclude_patterns):
            return False

        # Avoid common non-HTML file types based on extension
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in [
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.7z',
            '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.ico',
            '.mp3', '.wav', '.ogg',
            '.mp4', '.mov', '.avi', '.wmv',
            '.js', '.css', '.xml', '.json', '.txt', '.csv',
            '.gz', '.tar', '.bz2'
        ]):
            return False

        # Apply max URLs constraint
        if self.max_urls_to_crawl is not None and len(self.report.all_urls_crawled) >= self.max_urls_to_crawl:
            logger.debug(f"Max URLs limit ({self.max_urls_to_crawl}) reached. Skipping {defragged_url}.")
            return False

        # Check against seen URLs AFTER normalization and basic filtering
        if check_seen and defragged_url in self.seen_urls:
            return False

        return True

    async def _worker(self, worker_id: int, progress: Progress, task_id):
        """Processes URLs from the queue."""
        while True:
            # Get a URL from the queue, blocking until one is available
            url_to_crawl, depth, source_url = await self.queue.get()

            try:
                # Apply rate limiting before each request
                await asyncio.sleep(self.crawl_delay)

                # Acquire semaphore to limit concurrent requests
                async with self._limit_concurrent_requests:
                    # Re-check validity just before fetching
                    if url_to_crawl in self.processed_urls or not self._is_valid_url(url_to_crawl, check_seen=False):
                        progress.update(task_id, description=f"Worker {worker_id}: Skipped {urlparse(url_to_crawl).path} (already processed/invalid).")
                        continue
                    self.processed_urls.add(url_to_crawl)

                    if not await self._can_fetch(url_to_crawl):
                        logger.debug(f"Skipping {url_to_crawl} due to robots.txt policy.")
                        progress.update(task_id, description=f"Worker {worker_id}: Skipped {urlparse(url_to_crawl).path} (robots.txt)...")
                        continue

                    progress.update(task_id, description=f"Worker {worker_id}: Crawling [magenta]{urlparse(url_to_crawl).path}[/magenta]")

                    try:
                        async with _safe_get(
                            self.session, url_to_crawl,
                            allow_private=self.allow_private_hosts,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as response:
                            final_url = str(response.url).rstrip('/')
                            if final_url.endswith('/index.html'):
                                final_url = final_url.replace('/index.html', '')
                            if not self._is_valid_url(final_url, check_seen=False):
                                logger.warning("Redirect left scan scope; ignoring %s", final_url)
                                continue

                            if response.status >= 400:
                                self.report.broken_links[final_url].add(source_url)

                            content_type = response.headers.get('Content-Type', '').lower()
                            if response.status < 400 and 'text/html' in content_type:
                                self.report.all_urls_crawled.add(final_url)
                                self.crawled_count += 1
                                if response.content_length and response.content_length > MAX_RESPONSE_BYTES:
                                    logger.warning("Skipping oversized HTML response: %s", final_url)
                                    continue
                                raw_body = await _read_capped_bytes(response, MAX_RESPONSE_BYTES)
                                # Determine encoding using charset-normalizer if available
                                if _cn_from_bytes is not None:
                                    try:
                                        _best = _cn_from_bytes(raw_body).best()
                                        _quality = (getattr(_best, "quality", 0) or 0) / 100.0 if _best else 0.0
                                        encoding = _best.encoding if (_best and _quality >= 0.5) else "utf-8"
                                    except Exception:
                                        encoding = "utf-8"
                                else:
                                    encoding = "utf-8"

                                html = raw_body.decode(encoding, errors='ignore')
                                await self._process_html(html, final_url, depth)

                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        logger.debug(f"Network error crawling {url_to_crawl} (from {source_url}): {e}")
                        self.report.broken_links[url_to_crawl].add(source_url)
                    except Exception as e:
                        logger.error(f"Unexpected error crawling {url_to_crawl} (from {source_url}): {e}")

            except asyncio.CancelledError:
                logger.debug(f"Worker {worker_id} cancelled.")
                break
            finally:
                # Ensure task_done is called for every get()
                self.queue.task_done()
                # Update progress bar's completed count
                progress.update(task_id, advance=1)
                # Dynamically update total if new URLs are being discovered
                progress.update(task_id, total=len(self.seen_urls))

    async def _process_html(self, html: str, page_url: str, depth: int):
        """Parses HTML to find new links to add to the queue."""
        if depth >= self.max_depth:
            return

        soup = BeautifulSoup(html, 'lxml')
        # Find all link types: a[href], area[href], link[href], form[action]
        for tag in soup.find_all(['a', 'area', 'link', 'form', 'img']):
            # Prioritize href, then action, then src
            raw_url = tag.get('href') or tag.get('action') or tag.get('src')

            if tag.name == 'img' and tag.has_attr('srcset'):
                # Extract URLs from srcset
                for src_entry in tag['srcset'].split(','):
                    url_from_srcset = src_entry.strip().split(' ')[0]
                    await self._add_to_queue_if_valid(url_from_srcset, page_url, depth + 1)

            if raw_url:
                await self._add_to_queue_if_valid(raw_url, page_url, depth + 1)

    async def _add_to_queue_if_valid(self, raw_url: str, source_url: str, depth: int):
        """Add URL to queue if valid and not already seen."""
        abs_url = urljoin(source_url, raw_url)
        defragged_url, _ = urldefrag(abs_url)
        if defragged_url.endswith('/'):
            defragged_url = defragged_url.rstrip('/')
        if defragged_url.endswith('/index.html'):
            defragged_url = defragged_url.replace('/index.html', '')

        if self._is_valid_url(defragged_url):
            # Only add to queue and seen_urls if NOT already seen
            if defragged_url not in self.seen_urls:
                # Apply max URLs constraint before adding to queue
                if self.max_urls_to_crawl is not None and len(self.seen_urls) >= self.max_urls_to_crawl:
                    logger.debug(f"Max URLs limit ({self.max_urls_to_crawl}) reached for seen_urls. Not adding {defragged_url}.")
                    return

                self.seen_urls.add(defragged_url)
                await self.queue.put((defragged_url, depth, source_url))
                logger.debug(f"Added {defragged_url} to queue from {source_url}")
            else:
                logger.debug(f"URL {defragged_url} already seen or being processed.")

    async def _fetch_sitemap(self):
        """Attempts to fetch and parse sitemap.xml."""
        sitemap_url = urljoin(self.start_url, "/sitemap.xml")
        try:
            async with self._limit_concurrent_requests:
                async with _safe_get(
                    self.session, sitemap_url,
                    allow_private=self.allow_private_hosts,
                    timeout=10,
                ) as response:
                    if response.status == 200:
                        text = await _read_capped_text(response, MAX_SITEMAP_BYTES)
                        root = SafeET.fromstring(text)
                        urls = [
                            (node.text or "").strip()
                            for node in root.iter()
                            if node.tag.rsplit('}', 1)[-1] == 'loc' and (node.text or "").strip()
                        ]
                        if self.max_urls_to_crawl is not None:
                            urls = urls[: self.max_urls_to_crawl]
                        else:
                            urls = urls[:50000]
                        logger.info(f"Found {len(urls)} URLs in sitemap.xml.")
                        for url in urls:
                            await self._add_to_queue_if_valid(url, "sitemap.xml", 1)
                    else:
                        logger.info(f"Sitemap.xml not found or inaccessible at {sitemap_url} (Status: {response.status}).")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.info(f"Could not fetch sitemap.xml from {sitemap_url}: {e}")
        except Exception as e:
            logger.error(f"Error parsing sitemap.xml from {sitemap_url}: {e}")

    async def crawl(self) -> Set[str]:
        """Starts the crawling process."""
        headers = {'User-Agent': self.user_agent}
        # Configure aiohttp for better performance and error handling
        conn = _safe_connector(self.allow_private_hosts, limit_per_host=self.concurrency, force_close=True)

        async with aiohttp.ClientSession(headers=headers, connector=conn,
                                       timeout=aiohttp.ClientTimeout(total=45)) as session:
            self.session = session

            start_url_norm, _ = urldefrag(self.start_url)
            start_url_norm = start_url_norm.rstrip('/')
            if start_url_norm.endswith('/index.html'):
                start_url_norm = start_url_norm.replace('/index.html', '')

            # Initial URL check and queuing
            if self._is_valid_url(start_url_norm):
                self.seen_urls.add(start_url_norm)
                await self.queue.put((start_url_norm, 0, "start_url"))
            else:
                console.print(f"[bold red]Error: Start URL '{self.start_url}' is not valid or excluded, cannot initiate crawl.[/bold red]")
                return set()

            await self._fetch_sitemap()

            # Use Rich progress bar for visualization
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                         BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), console=console) as progress:
                self.progress_reporter = progress
                # Initial total for progress is based on already queued URLs
                crawl_task = progress.add_task("[cyan]Crawling website...", total=len(self.seen_urls))
                self.progress_tasks.append(crawl_task)

                # Start worker tasks
                workers = [asyncio.create_task(self._worker(i, progress, crawl_task)) for i in range(self.concurrency)]

                try:
                    # Wait for all tasks in the queue to be processed
                    await self.queue.join()
                finally:
                    # Cancel worker tasks once all queue items are processed
                    for w in workers:
                        w.cancel()
                    # Wait for worker tasks to actually finish cancelling
                    await asyncio.gather(*workers, return_exceptions=True)

                    # Update progress to 100% complete
                    progress.update(crawl_task, completed=progress.tasks[crawl_task].total,
                                  description="[green]Crawling complete.[/green]")

        return self.report.all_urls_crawled



################################################################################
# BEGIN fixer.py


logger = logging.getLogger(__name__)

INTERACTIVE_FIX_HEADER = "[bold cyan]Interactive Accessibility Remediation[/bold cyan]"
REMEDIATION_MAX_FILE_BYTES = 5 * 1024 * 1024
REMEDIATION_DIFF_LINE_LIMIT = 500
REMEDIATION_FILE_SUFFIXES = {".html", ".htm", ".css"}
AUTOCOMPLETE_FIELD_TOKENS = {
    "name", "honorific-prefix", "given-name", "additional-name", "family-name", "honorific-suffix",
    "nickname", "username", "new-password", "current-password", "one-time-code", "organization-title",
    "organization", "street-address", "address-line1", "address-line2", "address-line3", "address-level4",
    "address-level3", "address-level2", "address-level1", "country", "country-name", "postal-code",
    "cc-name", "cc-given-name", "cc-additional-name", "cc-family-name", "cc-number", "cc-exp",
    "cc-exp-month", "cc-exp-year", "cc-csc", "cc-type", "transaction-currency", "transaction-amount",
    "language", "bday", "bday-day", "bday-month", "bday-year", "sex", "url", "photo", "tel",
    "tel-country-code", "tel-national", "tel-area-code", "tel-local", "tel-local-prefix",
    "tel-local-suffix", "tel-extension", "email", "impp",
}


def _valid_autocomplete_value(value: str) -> bool:
    """Validate the common HTML autocomplete token grammar conservatively."""
    tokens = value.lower().split()
    if tokens in (["on"], ["off"]):
        return True
    if not tokens or len(tokens) > 5:
        return False
    if tokens and tokens[0].startswith("section-") and len(tokens[0]) > len("section-"):
        tokens.pop(0)
    if tokens and tokens[0] in {"shipping", "billing"}:
        tokens.pop(0)
    if tokens and tokens[0] in {"home", "work", "mobile", "fax", "pager"}:
        tokens.pop(0)
    if tokens and tokens[-1] == "webauthn":
        tokens.pop()
    return len(tokens) == 1 and tokens[0] in AUTOCOMPLETE_FIELD_TOKENS


def _remediation_template_risk(content: str, suffix: str) -> Optional[str]:
    """Return a reason when reparsing could damage a template or component file."""
    if suffix.lower() not in {".html", ".htm"}:
        return None
    markers = {
        "{{": "double-brace template expression",
        "{%": "template control block",
        "<%": "server-side template block",
        "<?php": "PHP template block",
        "<!--#": "server-side include",
        "asp-for=": "ASP.NET tag helper",
        "@model ": "Razor model directive",
        "*ngif=": "Angular structural directive",
        "[(ngmodel)]": "Angular two-way binding",
        "v-bind:": "Vue binding",
        "v-if=": "Vue directive",
    }
    lowered = content.lower()
    for marker, label in markers.items():
        if marker in lowered:
            return label
    return None


def _remediation_file_digest(path: Path) -> str:
    """Return the SHA-256 digest of one remediation target."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _uri_to_path(uri: str) -> Path:
    """Converts a file URI to a pathlib.Path object."""
    parsed = urlparse(uri)
    if not parsed.scheme:
        return Path(uri)
    if parsed.scheme.lower() != "file":
        raise ValueError("Only local file URIs can be remediated")
    if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
        raise ValueError("Remote or UNC file URIs are not remediated")
    # url2pathname handles drive letters correctly on Windows (e.g., /C:/ -> C:\)
    return Path(url2pathname(parsed.path))


def _copy_with_retry(src, dst, retries: int = 5, delay: float = 0.2):
    """Copy file with retry logic for locked files."""
    last_exc = None
    for _ in range(max(1, retries)):
        try:
            # Ensure target dir exists
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            return
        except Exception as e:
            last_exc = e
            time.sleep(delay)
    raise last_exc


class FixerTUI:
    """Interactive, review-first remediation engine for local HTML and CSS."""

    def __init__(self, report: AccessibilityReport):
        self.report = report
        # Filter for semi-automatic issues on local files
        self.semi_auto_issues = [
            i for i in self.report.issues
            if i.fix_type in {FixType.AUTOMATIC, FixType.SEMI_AUTOMATIC}
            and not i.fixed and i.file_path
        ]
        self.file_contents: Dict[str, str] = {}  # Store modified file contents
        self.original_file_contents: Dict[str, str] = {}  # For diff/comparison
        self.original_file_hashes: Dict[str, str] = {}
        self.original_file_boms: Dict[str, bool] = {}
        self.original_newlines: Dict[str, str] = {}
        self.saved_backups: Dict[str, Path] = {}
        self.saved_file_hashes: Dict[str, str] = {}
        self.transaction_restore_incomplete = False
        self.pending_issue_hashes: Set[str] = set()
        self.verification_results: Dict[str, Dict[str, Any]] = {}
        self.current_issue_index = 0

        target_path = Path(self.report.target).expanduser()
        if target_path.exists():
            resolved_target = target_path.resolve()
            self.allowed_root = resolved_target if resolved_target.is_dir() else resolved_target.parent
        else:
            self.allowed_root = None

    def _validate_file_target(self, path_obj: Path) -> Tuple[bool, str, Optional[Path]]:
        """Validate containment, type, size, and reparse-point policy."""
        if self.allowed_root is None:
            return False, "The scan target is not a local file or directory.", None
        try:
            if not path_obj.exists() or not path_obj.is_file():
                return False, "The target is not a regular file.", None
            if path_obj.is_symlink():
                return False, "Symbolic links are not modified.", None
            file_stat = path_obj.stat(follow_symlinks=False)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if reparse_flag and getattr(file_stat, "st_file_attributes", 0) & reparse_flag:
                return False, "Windows reparse points are not modified.", None
            if getattr(file_stat, "st_nlink", 1) > 1:
                return False, "Hard-linked files are not modified.", None
            resolved = path_obj.resolve(strict=True)
            common = os.path.commonpath([str(resolved), str(self.allowed_root)])
            if os.path.normcase(common) != os.path.normcase(str(self.allowed_root)):
                return False, "The file resolves outside the scanned target directory.", None
            if resolved.suffix.lower() not in REMEDIATION_FILE_SUFFIXES:
                return False, "Only local HTML, HTM, and CSS files are remediated.", None
            if file_stat.st_size > REMEDIATION_MAX_FILE_BYTES:
                return False, f"The file exceeds the {REMEDIATION_MAX_FILE_BYTES // (1024 * 1024)} MB remediation limit.", None
            return True, "", resolved
        except (OSError, ValueError) as exc:
            return False, f"File validation failed: {exc}", None

    def _get_file_content(self, file_path_uri: str) -> Optional[str]:
        """Lazy loads file content and stores original for diffing."""
        try:
            path_obj = _uri_to_path(file_path_uri)
        except ValueError as exc:
            console.print(f"[yellow]Skipped remediation target: {exc}[/yellow]")
            return None
        valid, reason, resolved = self._validate_file_target(path_obj)
        if not valid or resolved is None:
            console.print(f"[yellow]Skipped {path_obj}: {reason}[/yellow]")
            return None
        if file_path_uri not in self.file_contents:
            try:
                raw = resolved.read_bytes()
                if b"\x00" in raw:
                    raise ValueError("The file contains null bytes and is not treated as text")
                has_bom = raw.startswith(b"\xef\xbb\xbf")
                content = raw.decode("utf-8-sig")
                template_risk = _remediation_template_risk(content, resolved.suffix)
                if template_risk:
                    console.print(
                        f"[yellow]Skipped {resolved}: detected a {template_risk}. "
                        "Use the report guidance and edit the source template manually.[/yellow]"
                    )
                    return None
                newline = "\r\n" if b"\r\n" in raw else ("\r" if b"\r" in raw else "\n")
                self.file_contents[file_path_uri] = content
                self.original_file_contents[file_path_uri] = content
                self.original_file_hashes[file_path_uri] = hashlib.sha256(raw).hexdigest()
                self.original_file_boms[file_path_uri] = has_bom
                self.original_newlines[file_path_uri] = newline
            except (OSError, UnicodeError, ValueError) as exc:
                console.print(f"[red]Error: Could not safely read file {resolved} for remediation: {exc}[/red]")
                return None
        return self.file_contents[file_path_uri]

    def _display_issue(self, issue: AccessibilityIssue):
        """Displays a single issue in a rich format."""
        console.print(Panel(INTERACTIVE_FIX_HEADER, expand=False, border_style="cyan"))

        if not self.semi_auto_issues:
            console.print("[green]No unfixed semi-automatic issues pending review in local files.[/green]")
            return

        console.print(f"Issue [bold]{self.current_issue_index + 1}[/bold] of [bold]{len(self.semi_auto_issues)}[/bold] - [bold yellow]{issue.severity.value}[/bold yellow]")

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="bold yellow")
        table.add_column()
        table.add_row("Criterion:", f"{issue.criterion} - {issue.criterion_name} (Level {issue.level.value})")
        table.add_row("Description:", issue.description)
        table.add_row("Impact:", issue.impact)
        table.add_row("File:", f"[cyan]{issue.file_path}[/cyan]")
        if issue.line_number:
            table.add_row("Location:", f"Line {issue.line_number}")
        table.add_row("Selector:", f"[magenta]{issue.selector if issue.selector else 'N/A'}[/magenta]")
        console.print(table)

        if issue.element_html:
            console.print(Panel(
                Syntax(issue.element_html, "html", theme="monokai", line_numbers=False, word_wrap=True),
                title="Element HTML", border_style="yellow"
            ))
        if issue.context_html and issue.context_html != issue.element_html:
            console.print(Panel(
                Syntax(issue.context_html, "html", theme="monokai", line_numbers=False, word_wrap=True),
                title="Context HTML", border_style="green"
            ))

        console.print(f"\n[bold]Suggested remediation:[/bold] {issue.suggested_fix}")

    async def run(self):
        """Main loop for the interactive fixing session."""
        console.print(Panel(
            "Every source change requires review. Files are validated, diffs are shown, "
            "backups are unique, writes are atomic, and changed files are rescanned before you keep them.",
            title="Review-first workflow",
            border_style="cyan",
        ))

        self.semi_auto_issues = [
            i for i in self.report.issues
            if i.fix_type in {FixType.AUTOMATIC, FixType.SEMI_AUTOMATIC}
            and not i.fixed and i.file_path
        ]

        if not self.semi_auto_issues:
            console.print("[green]No guided source remediations are available for these local findings.[/green]")
            self._finalize_session()
            return

        while self.current_issue_index < len(self.semi_auto_issues):
            issue = self.semi_auto_issues[self.current_issue_index]

            # Ensure file content is loaded for this issue's file
            current_content = self._get_file_content(issue.file_path)
            if not current_content:
                logger.debug(f"Skipping interactive fix for issue {issue.issue_hash}: Could not read file {issue.file_path}")
                issue.fixed = False
                issue.fix_applied = "Skipped (Interactive Fixer): Could not read file for modification."
                self.current_issue_index += 1
                continue

            self._display_issue(issue)

            fix_successful = await self._handle_fix(issue)

            if fix_successful is None:  # User explicitly chose to exit fixer
                break

            # Move to the next issue
            self.current_issue_index += 1

        self._finalize_session()

    def _finalize_session(self):
        """Preview, transactionally save, verify, and optionally roll back changes."""
        changed = self._changed_files()
        if not changed:
            console.print("[green]\nNo source changes were generated.[/green]")
            console.print("[bold green]Interactive remediation session complete.[/bold green]")
            return

        self._display_diffs(changed)
        if not Confirm.ask(
            "[bold yellow]Save these reviewed changes and create rollback backups?[/bold yellow]",
            default=False,
        ):
            self._reset_pending_issue_state("Change was reviewed but not saved.")
            console.print("[yellow]Changes discarded. No source files were modified.[/yellow]")
            console.print("[bold green]Interactive remediation session complete.[/bold green]")
            return

        if not self._save_changes(changed):
            self._reset_pending_issue_state("Change could not be saved safely.")
            if self.transaction_restore_incomplete:
                console.print(
                    "[bold red]A failed transaction could not be restored completely. "
                    "Review the listed source files and backups manually.[/bold red]"
                )
            else:
                console.print("[red]No remediation transaction was retained.[/red]")
            console.print("[bold green]Interactive remediation session complete.[/bold green]")
            return

        verification_ok = self._verify_saved_changes(changed)
        prompt = (
            "Keep the saved changes?"
            if verification_ok
            else "Verification did not confirm every change. Keep the saved changes anyway?"
        )
        if Confirm.ask(f"[bold yellow]{prompt}[/bold yellow]", default=verification_ok):
            self._record_remediation_audit(changed)
            console.print("[bold green]Changes retained. Rollback backups remain beside the source files.[/bold green]")
        else:
            self._rollback_changes()

        console.print("[bold green]Interactive remediation session complete.[/bold green]")

    def _changed_files(self) -> Dict[str, str]:
        """Return file URI to changed content mappings."""
        return {
            file_uri: content
            for file_uri, content in self.file_contents.items()
            if content != self.original_file_contents.get(file_uri)
        }

    def _mark_issue_changed(self, issue: AccessibilityIssue, description: str):
        """Track a source change separately from final verification."""
        issue.fixed = True
        issue.fix_applied = description
        issue.additional_info["remediation_status"] = "Pending review and verification"
        self.pending_issue_hashes.add(issue.issue_hash)

    def _reset_pending_issue_state(self, reason: str):
        """Ensure discarded or rolled-back changes are never reported as fixed."""
        for issue in self.report.issues:
            if issue.issue_hash in self.pending_issue_hashes:
                issue.fixed = False
                issue.fix_applied = reason
                issue.additional_info["remediation_status"] = "Not retained"

    def _display_diffs(self, changed: Dict[str, str]):
        """Show bounded unified diffs before any disk write."""
        console.print(Panel(
            f"Reviewing {len(changed)} changed file(s). Large diffs are truncated for display only.",
            title="Remediation preview",
            border_style="yellow",
        ))
        for file_uri, new_content in changed.items():
            path_obj = _uri_to_path(file_uri)
            diff_iter = difflib.unified_diff(
                self.original_file_contents[file_uri].splitlines(),
                new_content.splitlines(),
                fromfile=f"{path_obj.name} (original)",
                tofile=f"{path_obj.name} (proposed)",
                lineterm="",
            )
            lines: List[str] = []
            truncated = False
            for index, line in enumerate(diff_iter):
                if index >= REMEDIATION_DIFF_LINE_LIMIT:
                    truncated = True
                    break
                lines.append(line)
            if truncated:
                lines.append(f"... diff display truncated after {REMEDIATION_DIFF_LINE_LIMIT} lines ...")
            console.print(Panel(
                Syntax("\n".join(lines) or "No textual diff available", "diff", word_wrap=True),
                title=str(path_obj),
                border_style="yellow",
            ))

    def _encode_changed_content(self, file_uri: str, content: str) -> bytes:
        """Preserve the original newline convention and UTF-8 BOM policy."""
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        newline = self.original_newlines.get(file_uri, "\n")
        if newline != "\n":
            normalized = normalized.replace("\n", newline)
        payload = normalized.encode("utf-8")
        if self.original_file_boms.get(file_uri):
            payload = b"\xef\xbb\xbf" + payload
        return payload

    @staticmethod
    def _atomic_replace_bytes(path_obj: Path, payload: bytes, stat_source: Optional[Path] = None):
        """Durably write bytes beside the target and atomically replace it."""
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path_obj.name}.wcag-",
            suffix=".tmp",
            dir=str(path_obj.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copystat(stat_source or path_obj, temporary_path, follow_symlinks=False)
            os.replace(temporary_path, path_obj)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _save_changes(self, changed: Dict[str, str]) -> bool:
        """Validate and save every changed file as one rollback-capable transaction."""
        self.transaction_restore_incomplete = False
        prepared: Dict[str, Tuple[Path, bytes]] = {}
        backups: Dict[str, Path] = {}
        written: Set[str] = set()
        try:
            for file_uri, new_content in changed.items():
                path_obj = _uri_to_path(file_uri)
                valid, reason, resolved = self._validate_file_target(path_obj)
                if not valid or resolved is None:
                    raise OSError(f"Unsafe remediation target {path_obj}: {reason}")
                current_digest = _remediation_file_digest(resolved)
                if current_digest != self.original_file_hashes.get(file_uri):
                    raise OSError(f"{resolved} changed after it was analyzed; refusing to overwrite it")
                prepared[file_uri] = (resolved, self._encode_changed_content(file_uri, new_content))

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            for file_uri, (path_obj, _) in prepared.items():
                backup_path = path_obj.with_name(
                    f"{path_obj.name}.wcag-backup.{stamp}.{secrets.token_hex(4)}"
                )
                _copy_with_retry(path_obj, backup_path)
                backups[file_uri] = backup_path

            for file_uri, (path_obj, payload) in prepared.items():
                if _remediation_file_digest(path_obj) != self.original_file_hashes.get(file_uri):
                    raise OSError(f"{path_obj} changed during remediation; refusing to overwrite it")
                self._atomic_replace_bytes(path_obj, payload, backups[file_uri])
                written.add(file_uri)

            self.saved_backups = backups
            self.saved_file_hashes = {
                file_uri: _remediation_file_digest(path_obj)
                for file_uri, (path_obj, _) in prepared.items()
            }
            for file_uri, (path_obj, _) in prepared.items():
                console.print(
                    f"[green]Saved {path_obj}[/green] "
                    f"[dim](backup: {backups[file_uri].name})[/dim]"
                )
            return True
        except Exception as exc:
            console.print(f"[red]Remediation transaction failed: {exc}[/red]")
            for file_uri, backup_path in backups.items():
                restore_path = prepared[file_uri][0]
                if file_uri not in written:
                    try:
                        backup_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue
                try:
                    self._atomic_replace_bytes(restore_path, backup_path.read_bytes(), backup_path)
                    backup_path.unlink(missing_ok=True)
                except Exception as restore_exc:
                    self.transaction_restore_incomplete = True
                    console.print(f"[bold red]Could not restore {restore_path}: {restore_exc}[/bold red]")
            return False

    def _verify_saved_changes(self, changed: Dict[str, str]) -> bool:
        """Rescan changed sources and verify that targeted criterion counts decrease."""
        table = Table(title="Post-remediation verification")
        table.add_column("File")
        table.add_column("Engine")
        table.add_column("Before")
        table.add_column("After")
        table.add_column("New")
        table.add_column("Result")
        overall_ok = True

        for file_uri in changed:
            path_obj = _uri_to_path(file_uri)
            try:
                content = path_obj.read_bytes().decode("utf-8-sig")
                if path_obj.suffix.lower() == ".css":
                    mode = AnalysisMode.CSS
                    after_issues, _ = CSSAnalyzer(self.report.wcag_level_tested).analyze(content, file_uri)
                    engine_name = "CSS"
                else:
                    mode = AnalysisMode.STATIC
                    after_issues, _ = StaticAnalyzer(self.report.wcag_level_tested).analyze(content, file_uri)
                    engine_name = "Static HTML"
                before_issues = [
                    issue for issue in self.report.issues
                    if issue.file_path == file_uri and issue.mode == mode
                ]
                before_count = len(before_issues)
                after_count = len(after_issues)
                def finding_key(item: AccessibilityIssue) -> Tuple[str, str]:
                    normalized = re.sub(r"'[^']*'|\"[^\"]*\"", "<value>", item.description)
                    normalized = re.sub(r"\d+", "#", normalized)
                    return item.criterion, re.sub(r"\s+", " ", normalized).strip().lower()

                before_finding_counts = Counter(finding_key(item) for item in before_issues)
                after_finding_counts = Counter(finding_key(item) for item in after_issues)
                introduced_keys = {
                    key: count - before_finding_counts.get(key, 0)
                    for key, count in after_finding_counts.items()
                    if count > before_finding_counts.get(key, 0)
                }
                introduced_count = sum(introduced_keys.values())
                introduced_findings = [
                    {
                        "criterion": criterion,
                        "normalized_description": description,
                        "instances": count,
                    }
                    for (criterion, description), count in sorted(introduced_keys.items())
                ]
                no_regression = after_count <= before_count and introduced_count == 0
                if not no_regression:
                    overall_ok = False

                pending = [
                    issue for issue in self.report.issues
                    if issue.issue_hash in self.pending_issue_hashes and issue.file_path == file_uri
                ]
                verification_detail: Dict[str, bool] = {}
                pending_by_criterion: DefaultDict[str, List[AccessibilityIssue]] = defaultdict(list)
                for issue in pending:
                    pending_by_criterion[issue.criterion].append(issue)
                for criterion, criterion_issues in pending_by_criterion.items():
                    before_for_criterion = sum(1 for item in before_issues if item.criterion == criterion)
                    after_for_criterion = sum(1 for item in after_issues if item.criterion == criterion)
                    verified_reduction = max(0, before_for_criterion - after_for_criterion)
                    for index, issue in enumerate(criterion_issues):
                        verified = index < verified_reduction
                        verification_detail[issue.issue_hash] = verified
                        issue.fixed = verified
                        issue.additional_info["remediation_status"] = (
                            "Verified by follow-up narrow automated scan"
                            if verified
                            else "Applied but not confirmed by follow-up narrow automated scan"
                        )
                        issue.additional_info["post_remediation_criterion_findings"] = after_for_criterion
                        if not verified:
                            overall_ok = False

                self.verification_results[file_uri] = {
                    "engine": engine_name,
                    "before_findings": before_count,
                    "after_findings": after_count,
                    "introduced_findings": introduced_findings,
                    "issue_verification": verification_detail,
                }
                result_label = "Verified" if no_regression and all(verification_detail.values()) else "Review"
                table.add_row(
                    path_obj.name, engine_name, str(before_count), str(after_count),
                    str(introduced_count), result_label,
                )
            except Exception as exc:
                overall_ok = False
                self.verification_results[file_uri] = {"error": str(exc)}
                table.add_row(path_obj.name, "Error", "?", "?", "?", "Review")

        console.print(table)
        console.print(
            "[dim]Verification is a repeat of the applicable narrow automated checks. "
            "It is not a conformance determination.[/dim]"
        )
        return overall_ok

    def _record_remediation_audit(self, changed: Dict[str, str]):
        """Record retained changes in the structured report audit trail."""
        for file_uri in changed:
            path_obj = _uri_to_path(file_uri)
            verification = self.verification_results.get(file_uri, {})
            for issue in self.report.issues:
                if issue.issue_hash not in self.pending_issue_hashes or issue.file_path != file_uri:
                    continue
                self.report.fixes_applied[str(path_obj)].append({
                    "issue_hash": issue.issue_hash,
                    "criterion": issue.criterion,
                    "description": issue.fix_applied or "",
                    "status": issue.additional_info.get("remediation_status", "Applied"),
                    "backup_path": str(self.saved_backups.get(file_uri, "")),
                    "original_sha256": self.original_file_hashes.get(file_uri, ""),
                    "retained_sha256": self.saved_file_hashes.get(file_uri, ""),
                    "verification": verification,
                })

    def _rollback_changes(self):
        """Restore all transaction backups unless a file changed externally."""
        restored_all = True
        for file_uri, backup_path in self.saved_backups.items():
            path_obj = _uri_to_path(file_uri)
            try:
                if _remediation_file_digest(path_obj) != self.saved_file_hashes.get(file_uri):
                    raise OSError("the file changed after remediation; automatic rollback was refused")
                self._atomic_replace_bytes(path_obj, backup_path.read_bytes(), backup_path)
                console.print(f"[yellow]Restored {path_obj} from {backup_path.name}[/yellow]")
            except Exception as exc:
                restored_all = False
                console.print(f"[bold red]Could not roll back {path_obj}: {exc}[/bold red]")
        if restored_all:
            self._reset_pending_issue_state("Change was rolled back after verification.")
            console.print("[green]All source files were restored.[/green]")
        else:
            self._reset_pending_issue_state("Rollback was incomplete; manual source review is required.")
            console.print("[bold red]Rollback was incomplete. Review the listed files and backups manually.[/bold red]")

    @staticmethod
    def _tag_end_offset(content: str, start: int) -> int:
        """Find the end of an HTML start tag while respecting quoted values."""
        quote: Optional[str] = None
        for index in range(start, len(content)):
            character = content[index]
            if quote:
                if character == quote:
                    quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character == ">":
                return index + 1
        raise ValueError("The selected element has an unterminated start tag")

    @staticmethod
    def _source_offset(content: str, tag: Tag) -> int:
        """Convert BeautifulSoup source coordinates to a character offset."""
        if tag.sourceline is None or tag.sourcepos is None:
            raise ValueError("The parser did not expose source coordinates")
        lines = content.splitlines(keepends=True)
        if tag.sourceline < 1 or tag.sourceline > len(lines):
            raise ValueError("The selected element has invalid source coordinates")
        offset = sum(len(line) for line in lines[:tag.sourceline - 1]) + tag.sourcepos
        if offset >= len(content) or content[offset] != "<":
            line_start = sum(len(line) for line in lines[:tag.sourceline - 1])
            line_end = line_start + len(lines[tag.sourceline - 1])
            match = re.search(rf"<\s*{re.escape(tag.name)}\b", content[line_start:line_end], re.IGNORECASE)
            if not match:
                raise ValueError("The selected element could not be mapped to source text")
            offset = line_start + match.start()
        return offset

    def _locate_source_tag(
        self,
        content: str,
        selector: str,
        expected_name: Optional[str] = None,
    ) -> Tuple[Tag, int, int]:
        """Locate exactly one source element and return its start-tag span."""
        if not selector:
            raise ValueError("The finding does not include a source selector")
        soup_obj = BeautifulSoup(content, "html.parser")
        try:
            matches = soup_obj.select(selector)
        except Exception as exc:
            if exc.__class__.__name__ == "SelectorSyntaxError":
                raise ValueError(f"The source selector is invalid: {selector}") from exc
            raise
        if len(matches) != 1:
            raise ValueError(f"The selector matched {len(matches)} elements instead of exactly one")
        target = matches[0]
        if expected_name and target.name.lower() != expected_name.lower():
            raise ValueError(f"Expected <{expected_name}> but found <{target.name}>")
        start = self._source_offset(content, target)
        end = self._tag_end_offset(content, start)
        return target, start, end

    @staticmethod
    def _replace_start_tag_attribute(opening_tag: str, name: str, value: str) -> str:
        """Set one HTML attribute without reserializing the document."""
        escaped_value = html_lib.escape(value, quote=True)
        pattern = re.compile(
            rf"(?i)(?P<space>\s){re.escape(name)}(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?"
        )
        replacement = rf'\g<space>{name}="{escaped_value}"'
        if pattern.search(opening_tag):
            return pattern.sub(replacement, opening_tag, count=1)
        insertion = opening_tag.rfind("/>")
        if insertion < 0:
            insertion = opening_tag.rfind(">")
        if insertion < 0:
            raise ValueError("The selected element has no closing angle bracket")
        return opening_tag[:insertion] + f' {name}="{escaped_value}"' + opening_tag[insertion:]

    def _set_source_attribute(
        self,
        file_uri: str,
        selector: str,
        name: str,
        value: str,
        expected_name: Optional[str] = None,
    ) -> bool:
        """Apply a formatting-preserving HTML attribute edit in memory."""
        content = self._get_file_content(file_uri)
        if content is None:
            return False
        try:
            _, start, end = self._locate_source_tag(content, selector, expected_name)
            updated_tag = self._replace_start_tag_attribute(content[start:end], name, value)
            if updated_tag == content[start:end]:
                return False
            self.file_contents[file_uri] = content[:start] + updated_tag + content[end:]
            return True
        except (ValueError, NotImplementedError) as exc:
            console.print(f"[red]Could not safely locate the exact element: {exc}[/red]")
            return False

    @staticmethod
    def _prompt_text_is_safe(value: str, maximum: int = 1000) -> bool:
        """Reject empty, excessively long, or control-character remediation text."""
        return bool(
            value.strip()
            and len(value) <= maximum
            and not any(ord(character) < 32 and character not in {"\t"} for character in value)
        )

    def _insert_before_selector(self, file_uri: str, selector: str, markup: str) -> bool:
        """Insert trusted generated markup before exactly one selected element."""
        content = self._get_file_content(file_uri)
        if content is None:
            return False
        try:
            _, start, _ = self._locate_source_tag(content, selector)
            line_start = content.rfind("\n", 0, start) + 1
            indentation = content[line_start:start]
            if indentation.strip():
                insertion = markup
            else:
                insertion = markup + self.original_newlines.get(file_uri, "\n") + indentation
            self.file_contents[file_uri] = content[:start] + insertion + content[start:]
            return True
        except (ValueError, NotImplementedError) as exc:
            console.print(f"[red]Could not safely insert source markup: {exc}[/red]")
            return False

    async def _handle_fix(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handles the user interaction for a specific fix type based on criterion."""
        handler_map = {
            "1.1.1": self._handle_alt_text,
            "1.3.5": self._handle_autocomplete,
            "2.4.1": self._handle_skip_link,
            "2.4.2": self._handle_page_title,
            "2.4.6": self._handle_iframe_title,
            "2.4.7": self._handle_outline_none,
            "3.1.1": self._handle_document_language,
            "3.3.2": self._handle_form_label,
        }

        handler = handler_map.get(issue.criterion, self._handle_default)
        return await handler(issue)

    async def _handle_default(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Default handler for unknown semi-automatic fixes."""
        console.print("\n[yellow]This fix requires manual intervention or is not yet automated by the tool.[/yellow]")
        action = await questionary.select(
            "What would you like to do?",
            choices=["Mark as Fixed (Manual Verification)", "Skip", "Exit Fixer"]
        ).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Mark as Fixed (Manual Verification)":
            issue.fixed = True
            issue.fix_applied = "Manually verified and marked as fixed."
            issue.additional_info["remediation_status"] = "Manually verified; no source edit was made"
            console.print("[green]✓ Issue manually marked as fixed.[/green]")
            await asyncio.sleep(1)
            return True
        return False  # Skipped

    async def _handle_document_language(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Guide the user through declaring the page language."""
        action = await questionary.select(
            "Choose the page's primary language:",
            choices=["English (en)", "Spanish (es)", "French (fr)", "Enter a BCP 47 tag", "Skip", "Exit Fixer"],
        ).ask_async()
        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False
        language_map = {"English (en)": "en", "Spanish (es)": "es", "French (fr)": "fr"}
        language = language_map.get(action, "")
        if action == "Enter a BCP 47 tag":
            response = await questionary.text("Enter a BCP 47 language tag, such as en-US:").ask_async()
            language = (response or "").strip()
        if not _valid_language_tag(language):
            console.print("[yellow]That value is not accepted as BCP 47 syntax. No change was made.[/yellow]")
            return False
        if self._set_source_attribute(issue.file_path, issue.selector or "html", "lang", language, "html"):
            self._mark_issue_changed(issue, f"Set the document language to '{language}'.")
            console.print(f"[green]Proposed document language: {language}[/green]")
            return True
        return False

    async def _handle_skip_link(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Add a reviewed skip link when a unique main-content target is available."""
        action = await questionary.select(
            "A skip link is missing. Choose an action:",
            choices=["Add skip link to detected main content", "Skip", "Exit Fixer"],
        ).ask_async()
        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False
        content = self._get_file_content(issue.file_path)
        if content is None:
            return False
        soup_obj = BeautifulSoup(content, "html.parser")
        target = soup_obj.find("main") or soup_obj.select_one('[role="main"]')
        if target is None:
            for selector in ("#main-content", "#main", "#content", "article"):
                target = soup_obj.select_one(selector)
                if target is not None:
                    break
        if target is None:
            console.print("[yellow]No unique main-content target was detected. Add a landmark manually first.[/yellow]")
            return False
        target_selector = generate_css_selector(target)
        target_id = str(target.get("id") or "").strip()
        if not target_id:
            base = "main-content"
            target_id = base
            suffix = 2
            while soup_obj.find(id=target_id):
                target_id = f"{base}-{suffix}"
                suffix += 1
            if not self._set_source_attribute(issue.file_path, target_selector, "id", target_id, target.name):
                return False

        content = self.file_contents[issue.file_path]
        newline = self.original_newlines.get(issue.file_path, "\n")
        style_id = "wcag-remediation-skip-link-style"
        if style_id not in content:
            style_markup = (
                f'{newline}<style id="{style_id}">{newline}'
                ".wcag-remediation-skip-link { position: absolute; left: -10000px; "
                "top: auto; width: 1px; height: 1px; overflow: hidden; }"
                f"{newline}.wcag-remediation-skip-link:focus {{ position: fixed; left: 1rem; top: 1rem; "
                "width: auto; height: auto; padding: .75rem 1rem; overflow: visible; "
                "background: #fff; color: #000; border: 3px solid #000; z-index: 2147483647; }"
                f"{newline}</style>{newline}"
            )
            closing_head = re.search(r"</head\s*>", content, re.IGNORECASE)
            if closing_head:
                content = content[:closing_head.start()] + style_markup + content[closing_head.start():]

        try:
            _, _, body_end = self._locate_source_tag(content, "body", "body")
        except (ValueError, NotImplementedError) as exc:
            console.print(f"[red]Could not safely locate <body>: {exc}[/red]")
            return False
        link_markup = (
            f'{newline}<a class="wcag-remediation-skip-link" href="#{html_lib.escape(target_id, quote=True)}">'
            f"Skip to main content</a>"
        )
        content = content[:body_end] + link_markup + content[body_end:]
        self.file_contents[issue.file_path] = content
        self._mark_issue_changed(issue, f"Added a skip link targeting '#{target_id}'.")
        console.print(f"[green]Proposed a skip link targeting #{target_id}.[/green]")
        return True

    async def _handle_alt_text(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handler for 1.1.1 (Non-text Content) alt text issues."""
        action = await questionary.select(
            "Choose an action for this missing/problematic alt text:",
            choices=["Provide descriptive alt text", "Mark as decorative (alt='')", "Skip", "Exit Fixer"]
        ).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False

        new_alt_text = ""
        if action == "Provide descriptive alt text":
            response = await questionary.text("Enter descriptive alt text:", default="").ask_async()
            if response is None or not self._prompt_text_is_safe(response):
                console.print("[yellow]Alt text must be non-empty, reasonably sized, and free of control characters.[/yellow]")
                return False
            new_alt_text = response.strip()

        expected = BeautifulSoup(issue.element_html or "", "html.parser").find()
        expected_name = expected.name if expected else None
        if self._set_source_attribute(
            issue.file_path, issue.selector or "", "alt", new_alt_text, expected_name
        ):
            description = "Marked the image as decorative with empty alternative text." if not new_alt_text else "Added reviewed alternative text."
            self._mark_issue_changed(issue, description)
            console.print(f"[green]✓ Fix applied. Alt text set to: '{new_alt_text}'[/green]")
            return True
        return False

    async def _handle_page_title(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handler for 2.4.2 (Page Titled) issues."""
        action = await questionary.select("Page <title> is missing or empty. Action:",
                                         choices=["Provide page title", "Skip", "Exit Fixer"]).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False

        title_text = await questionary.text("Enter descriptive page title:", default="").ask_async()
        if not title_text or not self._prompt_text_is_safe(title_text, maximum=300):
            console.print("[yellow]No valid title provided. Skipping fix for this issue.[/yellow]")
            return False
        title_text = title_text.strip()

        file_content = self._get_file_content(issue.file_path)
        if file_content is None:
            return False
        escaped_title = html_lib.escape(title_text)
        soup_obj = BeautifulSoup(file_content, "html.parser")
        titles = soup_obj.find_all("title")
        try:
            if len(titles) == 1:
                _, _, start_tag_end = self._locate_source_tag(file_content, "title", "title")
                closing = re.search(r"</title\s*>", file_content[start_tag_end:], re.IGNORECASE)
                if not closing:
                    raise ValueError("The title element has no closing tag")
                closing_start = start_tag_end + closing.start()
                updated = file_content[:start_tag_end] + escaped_title + file_content[closing_start:]
            elif len(titles) == 0:
                _, _, head_start_end = self._locate_source_tag(file_content, "head", "head")
                newline = self.original_newlines.get(issue.file_path, "\n")
                updated = file_content[:head_start_end] + f"{newline}<title>{escaped_title}</title>" + file_content[head_start_end:]
            else:
                raise ValueError("Multiple title elements require manual review")
        except (ValueError, NotImplementedError) as exc:
            console.print(f"[red]Could not safely update the page title: {exc}[/red]")
            return False

        if updated != file_content:
            self.file_contents[issue.file_path] = updated
            self._mark_issue_changed(issue, f"Set the page title to '{title_text}'.")
            console.print("[green]✓ Fix applied.[/green]")
            return True
        return False

    async def _handle_autocomplete(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handler for 1.3.5 (Identify Input Purpose) issues."""
        suggested_autocomplete = issue.additional_info.get('suggested_autocomplete', 'name')
        action = await questionary.select(
            f"Input field needs an `autocomplete` attribute. Suggested: `{suggested_autocomplete}`.",
            choices=[f"Add autocomplete='{suggested_autocomplete}'", "Enter custom autocomplete", "Skip", "Exit Fixer"]
        ).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False

        autocomplete_value = suggested_autocomplete
        if action == "Enter custom autocomplete":
            response = await questionary.text("Enter custom `autocomplete` value:").ask_async()
            if not response or not response.strip():
                console.print("[yellow]No valid custom autocomplete value provided. Skipping.[/yellow]")
                return False
            autocomplete_value = response.strip()
        if not _valid_autocomplete_value(autocomplete_value):
            console.print("[yellow]That value is not accepted by the conservative autocomplete-token validator.[/yellow]")
            return False

        expected = BeautifulSoup(issue.element_html or "", "html.parser").find()
        expected_name = expected.name if expected else None
        if self._set_source_attribute(
            issue.file_path, issue.selector or "", "autocomplete", autocomplete_value, expected_name
        ):
            self._mark_issue_changed(issue, f"Set autocomplete to '{autocomplete_value}'.")
            console.print(f"[green]✓ Fix applied: autocomplete='{autocomplete_value}'[/green]")
            return True
        return False

    async def _handle_form_label(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handler for 3.3.2 (Labels or Instructions) issues."""
        action = await questionary.select(
            "Form control lacks an accessible label. How to fix?",
            choices=["Add <label for='ID'>", "Add aria-label", "Skip", "Exit Fixer"]
        ).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False

        file_content = self._get_file_content(issue.file_path)
        if file_content is None:
            return False
        try:
            target_element, _, _ = self._locate_source_tag(file_content, issue.selector or "")
        except (ValueError, NotImplementedError) as exc:
            console.print(f"[red]Could not safely locate the exact form control: {exc}[/red]")
            return False

        if action == "Add <label for='ID'>":
            new_id = target_element.get('id')
            if not new_id:
                existing_ids = {
                    str(tag.get("id")) for tag in BeautifulSoup(file_content, "html.parser").find_all(id=True)
                }
                while True:
                    new_id = f"a11y-control-{secrets.randbelow(900000) + 100000}"
                    if new_id not in existing_ids:
                        break

            label_text = await questionary.text("Enter label text for the control:").ask_async()
            if not label_text or not self._prompt_text_is_safe(label_text, maximum=300):
                console.print("[yellow]No label text provided. Skipping fix.[/yellow]")
                return False
            label_text = label_text.strip()

            if not target_element.get("id") and not self._set_source_attribute(
                issue.file_path, issue.selector or "", "id", str(new_id), target_element.name
            ):
                return False
            label_markup = (
                f'<label for="{html_lib.escape(str(new_id), quote=True)}">'
                f"{html_lib.escape(label_text)}</label>"
            )
            if self._insert_before_selector(issue.file_path, issue.selector or "", label_markup):
                self._mark_issue_changed(issue, f"Added a visible label associated with control '{new_id}'.")
                console.print("[green]✓ Fix applied. Label added.[/green]")
                return True

        elif action == "Add aria-label":
            aria_label_text = await questionary.text("Enter text for `aria-label`:").ask_async()
            if not aria_label_text or not self._prompt_text_is_safe(aria_label_text, maximum=300):
                console.print("[yellow]No `aria-label` text provided. Skipping fix.[/yellow]")
                return False
            aria_label_text = aria_label_text.strip()
            if self._set_source_attribute(
                issue.file_path, issue.selector or "", "aria-label", aria_label_text, target_element.name
            ):
                self._mark_issue_changed(issue, "Added a reviewed aria-label to the form control.")
                console.print("[green]✓ Fix applied. `aria-label` added.[/green]")
                return True
        return False

    async def _handle_iframe_title(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handler for 2.4.6 (Headings and Labels) iframe title issues."""
        action = await questionary.select(
            "<iframe> is missing a descriptive `title`. How to fix?",
            choices=["Add title attribute", "Skip", "Exit Fixer"]
        ).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False

        if action == "Add title attribute":
            title_text = await questionary.text("Enter descriptive title for the iframe:").ask_async()
            if not title_text or not self._prompt_text_is_safe(title_text, maximum=300):
                console.print("[yellow]No title text provided. Skipping fix.[/yellow]")
                return False
            title_text = title_text.strip()
            if self._set_source_attribute(
                issue.file_path, issue.selector or "", "title", title_text, "iframe"
            ):
                self._mark_issue_changed(issue, "Added a reviewed title to the iframe.")
                console.print("[green]✓ Fix applied. Iframe title added.[/green]")
                return True
        return False

    async def _handle_outline_none(self, issue: AccessibilityIssue) -> Optional[bool]:
        """Handler for 2.4.7 (Focus Visible) outline:none issues from CSS."""
        action = await questionary.select(
            "CSS property `outline: none;` found. How to fix?",
            choices=["Remove outline property", "Skip", "Exit Fixer"]
        ).ask_async()

        if action == "Exit Fixer":
            return None
        if action == "Skip":
            return False

        file_content = self._get_file_content(issue.file_path)
        if not file_content:
            return False

        # CSS modification is more complex than HTML
        try:
            stylesheet = cssutils.parseString(file_content)
            modified = False
            target_selector = issue.selector or issue.additional_info.get('css_selector', '')
            for rule in stylesheet.cssRules:
                if rule.type == CSSStyleRule.STYLE_RULE:
                    if rule.selectorText == target_selector and rule.style.getProperty('outline'):
                        if action == "Remove outline property":
                            rule.style.removeProperty('outline')
                            console.print(f"[green]Removed `outline` property from rule for '{rule.selectorText}'[/green]")
                            modified = True
                            break

                elif isinstance(rule, cssutils.css.CSSMediaRule):
                    selector_parts = target_selector.split('{', 1)
                    if len(selector_parts) == 2:
                        media_query_text = selector_parts[0].strip().replace("@media", "").strip()
                        rule_selector_text = selector_parts[1].replace('}', '').strip()

                        if rule.media.mediaText.strip() == media_query_text:
                            for media_rule_inner in rule.cssRules:
                                if media_rule_inner.type == CSSStyleRule.STYLE_RULE and media_rule_inner.selectorText == rule_selector_text:
                                    if media_rule_inner.style.getProperty('outline'):
                                        media_rule_inner.style.removeProperty('outline')
                                        console.print(f"[green]Removed `outline` property from rule for '{rule_selector_text}' within media query.[/green]")
                                        modified = True
                                        break
                        if modified:
                            break

            if modified:
                self.file_contents[issue.file_path] = stylesheet.cssText.decode('utf-8')
                self._mark_issue_changed(
                    issue,
                    f"Removed the outline suppression from CSS selector '{issue.selector}'.",
                )
                console.print("[green]✓ Fix applied to CSS file.[/green]")
                return True
            else:
                console.print("[red]Could not find or modify the specific CSS property in the file. Skipping fix (manual required?).[/red]")
                return False
        except Exception as e:
            console.print(f"[red]Error modifying CSS file {issue.file_path}: {e}[/red]")
            logger.error(f"Error in _handle_outline_none for {issue.file_path}: {e}", exc_info=True)
            return False



################################################################################
# Reporting and application controller

try:
    from PIL import Image as PIL_Image
except ImportError:
    PIL_Image = None

try:
    from fpdf import FPDF, XPos, YPos
except ImportError:
    FPDF = XPos = YPos = None


logger = logging.getLogger(__name__)

# Quiet logging setup. No directories or files are created at import time.
LOG_DIR: Optional[str] = None
LOG_FILE: Optional[str] = None

def _install_quiet_logging(output_dir: Union[str, Path]):
    """Install quiet logging configuration."""
    global LOG_DIR, LOG_FILE
    LOG_DIR = str(Path(output_dir).resolve())
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    LOG_FILE = str(Path(LOG_DIR) / "run.log")
    # Remove ALL handlers from root and named loggers
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for _, lg in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(lg, logging.Logger):
            for h in list(lg.handlers):
                lg.removeHandler(h)
            lg.propagate = True

    # Console level from env (default ERROR = super quiet)
    _console_level_name = os.getenv("A11Y_LOG_LEVEL", "ERROR").upper()
    _console_level = getattr(logging, _console_level_name, logging.ERROR)

    root.setLevel(logging.DEBUG)  # capture all to file
    console_handler = logging.StreamHandler()
    console_handler.setLevel(_console_level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.getLogger(__name__).debug("Quiet logging installed: console=%s, file=%s", _console_level_name, LOG_FILE)

# ==============================================================================
# REPORTING ENGINE
# ==============================================================================

class ReportGenerator:
    """Generates reports in multiple formats."""

    def __init__(self, report: AccessibilityReport, output_dir: Path):
        self.report = report
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Ensure screenshot directory exists
        if self.report.screenshot_dir:
            self.report.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, formats: List[str]):
        """Generate all requested report formats."""
        requested = [value.strip().lower() for value in formats if value.strip()]
        supported = {'html', 'json', 'csv', 'md', 'pdf', 'junit'}
        unsupported = sorted(set(requested) - supported)
        if unsupported:
            raise ValueError(f"Unsupported web report format(s): {', '.join(unsupported)}")
        if not requested:
            raise ValueError("Select at least one web report format")
        console.print(Panel(f"[bold]Generating reports in: {', '.join(requested)}[/bold]", border_style="blue"))
        generators = [
            ('json', self.generate_json),
            ('csv', self.generate_csv),
            ('md', self.generate_markdown),
            ('html', self.generate_html),
            ('pdf', self.generate_pdf),
            ('junit', self.generate_junit_xml),
        ]
        failures: List[str] = []
        for name, generator in generators:
            if name not in requested:
                continue
            try:
                generator()
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                console.print(f"[red]Failed to generate {name} report: {exc}[/red]")
                logger.exception("Report generation failed for %s", name)
        if failures:
            raise RuntimeError("One or more reports failed: " + "; ".join(failures))

    def generate_json(self):
        """Generate JSON report."""
        path = self.output_dir / "report.json"
        report_dict = asdict(self.report)

        # Convert enums to strings for JSON serialization
        for issue in report_dict.get("issues", []):
            issue['level'] = _enum_value_safe(issue['level'])
            issue['severity'] = _enum_value_safe(issue['severity'])
            issue['fix_type'] = _enum_value_safe(issue['fix_type'])
            issue['mode'] = _enum_value_safe(issue['mode'])

            if issue['screenshot_path']:
                if self.report.screenshot_dir:
                    issue['screenshot_path'] = str(
                        Path(self.report.screenshot_dir.name) / Path(issue['screenshot_path']).name
                    )
                else:
                    issue['screenshot_path'] = Path(issue['screenshot_path']).name

        for passed in report_dict.get("passed_checks", []):
            passed['level'] = _enum_value_safe(passed['level'])
            passed['mode'] = _enum_value_safe(passed['mode'])

        report_dict['wcag_level_tested'] = report_dict['wcag_level_tested'].value
        report_dict['all_files_analyzed'] = list(report_dict['all_files_analyzed'])
        report_dict['all_urls_crawled'] = list(report_dict['all_urls_crawled'])
        # Convert broken_links sets to lists for JSON serialization
        if report_dict.get('broken_links'):
            report_dict['broken_links'] = {k: list(v) if isinstance(v, set) else v
                                            for k, v in report_dict['broken_links'].items()}
        if report_dict['screenshot_dir']:
            report_dict['screenshot_dir'] = str(report_dict['screenshot_dir']) if not isinstance(report_dict['screenshot_dir'], str) else report_dict['screenshot_dir']

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(report_dict, f, indent=2)
            console.print(f"✓ JSON report saved to [cyan]{path}[/cyan]")
        except Exception as e:
            raise RuntimeError(f"JSON report generation failed: {e}") from e

    def generate_csv(self):
        """Generate CSV report."""
        path = self.output_dir / "report.csv"
        issues_data = []

        for issue in self.report.issues:
            issue_dict = asdict(issue)
            # Convert enums to string
            issue_dict['level'] = _enum_value_safe(issue_dict['level'])
            issue_dict['severity'] = _enum_value_safe(issue_dict['severity'])
            issue_dict['fix_type'] = _enum_value_safe(issue_dict['fix_type'])
            issue_dict['mode'] = _enum_value_safe(issue_dict['mode'])
            if issue_dict['screenshot_path'] and self.report.screenshot_dir:
                issue_dict['screenshot_path'] = str(Path(self.report.screenshot_dir.name) / Path(issue_dict['screenshot_path']).name)
            issue_dict['additional_info'] = json.dumps(issue_dict['additional_info'])
            # Neutralize CSV formula/macro injection in every string cell (F2 / CWE-1236).
            issue_dict = {k: _csv_safe(v) for k, v in issue_dict.items()}
            issues_data.append(issue_dict)

        cols = [
            'severity', 'criterion', 'criterion_name', 'level', 'mode', 'description',
            'impact', 'url', 'file_path', 'line_number', 'selector', 'element_html',
            'suggested_fix', 'fix_type', 'fixed', 'fix_applied', 'screenshot_path', 'additional_info'
        ]
        try:
            with path.open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=cols, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(issues_data)
            console.print(f"✓ CSV report saved to [cyan]{path}[/cyan]")
        except Exception as e:
            raise RuntimeError(f"CSV report generation failed: {e}") from e

    def generate_markdown(self):
        """Generate Markdown report."""
        path = self.output_dir / "report.md"
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"# Accessibility Audit Report for {_md_cell(self.report.target)}\n\n")
                f.write(f"**Timestamp:** {_md_cell(self.report.timestamp)}\n")
                f.write(f"**WCAG Level Tested:** {self.report.wcag_level_tested.value}\n\n")
                f.write("## Summary\n\n")
                f.write("| Metric | Value |\n|---|---|\n")

                for key, val in self.report.summary.items():
                    if isinstance(val, dict):
                        f.write(f"| {key.replace('_', ' ').title()} | {_md_cell(json.dumps(val))} |\n")
                    else:
                        f.write(f"| {key.replace('_', ' ').title()} | {_md_cell(val)} |\n")

                f.write("\n## Issues Found\n\n")
                f.write("| Severity | Criterion | Description | Location | Fix Type | Fixed |\n")
                f.write("|---|---|---|---|---|---|\n")

                for issue in sorted(self.report.issues, key=lambda i: (list(IssueSeverity).index(i.severity), i.level.value, i.criterion)):
                    location_raw = (issue.url or issue.file_path or "")
                    location_display = (location_raw[:50] + "...") if len(location_raw) > 50 else location_raw
                    f.write(f"| {issue.severity.value} | {_md_cell(issue.criterion)} | {_md_cell(issue.description)} | {_md_cell(location_display)} | {issue.fix_type.value} | {'Yes' if issue.fixed else 'No'} |\n")

            console.print(f"✓ Markdown report saved to [cyan]{path}[/cyan]")
        except Exception as e:
            raise RuntimeError(f"Markdown report generation failed: {e}") from e

    @staticmethod
    def _normalize_text(s: Optional[str]) -> str:
        """Collapse instance-specific bits (quoted strings, numbers) so descriptions group by root cause."""
        if not s:
            return ""
        s = re.sub(r"'[^']*'|\"[^\"]*\"", "…", s)
        s = re.sub(r"\d+", "#", s)
        return re.sub(r"\s+", " ", s).strip().lower()

    @staticmethod
    def _element_signature(issue: AccessibilityIssue) -> str:
        """A structural signature for the offending element: tag + attribute names (+ role/type), ignoring values."""
        html = issue.element_html or ""
        m = re.match(r"\s*<\s*([a-zA-Z0-9]+)([^>]*)>", html)
        if m:
            tag = m.group(1).lower()
            attr_blob = m.group(2)
            attrs = sorted(set(name.lower() for name in re.findall(r"([a-zA-Z_:][-\w:]*)\s*=", attr_blob)))
            sig = f"{tag}[{','.join(attrs)}]"
            role = re.search(r"role\s*=\s*['\"]([^'\"]+)", attr_blob)
            typ = re.search(r"type\s*=\s*['\"]([^'\"]+)", attr_blob)
            if role:
                sig += f" role={role.group(1).lower()}"
            if typ:
                sig += f" type={typ.group(1).lower()}"
            return sig
        sel = (issue.selector or "")
        sel = re.sub(r":nth-of-type\(\d+\)", "", sel)
        sel = re.sub(r"#[\w-]+", "#id", sel)
        return sel.strip()

    def _rollup_findings(self) -> List[Dict[str, Any]]:
        """Group issue instances by root cause (criterion + normalized description + element signature).

        This is the key differentiator over per-instance reports: a template problem that appears on
        300 pages becomes ONE finding ("fix once, resolves 300 pages") instead of 300 near-identical rows.
        """
        groups: Dict[Any, Dict[str, Any]] = {}
        order = list(IssueSeverity)
        for issue in self.report.issues:
            key = (issue.criterion, self._normalize_text(issue.description), self._element_signature(issue))
            g = groups.get(key)
            if g is None:
                g = groups[key] = {
                    'criterion': issue.criterion, 'criterion_name': issue.criterion_name,
                    'level': issue.level, 'severity': issue.severity, 'mode': issue.mode,
                    'fix_type': issue.fix_type, 'description': issue.description, 'impact': issue.impact,
                    'suggested_fix': issue.suggested_fix, 'element_html': issue.element_html,
                    'context_html': issue.context_html, 'selector': issue.selector,
                    'screenshot_path': issue.screenshot_path,
                    'instances': 0, 'locations': [], 'pages': set(), 'open': False,
                }
            g['instances'] += 1
            loc = issue.url or issue.file_path
            if loc:
                g['pages'].add(loc)
            if len(g['locations']) < 250:
                g['locations'].append({'url': loc, 'line': issue.line_number})
            if order.index(issue.severity) < order.index(g['severity']):
                g['severity'] = issue.severity
            if g['element_html'] is None and issue.element_html:
                g['element_html'] = issue.element_html
            if g['screenshot_path'] is None and issue.screenshot_path:
                g['screenshot_path'] = issue.screenshot_path
            if not issue.fixed:
                g['open'] = True
        result = list(groups.values())
        for g in result:
            g['page_count'] = len(g['pages'])
        result.sort(key=lambda g: (order.index(g['severity']), -g['page_count'], -g['instances']))
        return result

    def generate_html(self):
        """Generate executive-grade interactive HTML report."""
        path = self.output_dir / "report.html"
        # Copy screenshots to report directory
        if self.report.screenshot_dir:
            target_screenshot_dir = self.output_dir / self.report.screenshot_dir.name
            if self.report.screenshot_dir.exists():
                try:
                    import shutil
                    shutil.copytree(self.report.screenshot_dir, target_screenshot_dir, dirs_exist_ok=True)
                except Exception:
                    logger.debug('Some screenshots were locked; skipped copying a few files.')

        # --- Compute all data for the template ---
        def _esc(text):
            if not text:
                return ""
            # Encode quotes too so values are safe in HTML attribute context (F1).
            return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace('"', "&quot;").replace("'", "&#39;"))

        total_issues = len(self.report.issues)
        total_passed = len(self.report.passed_checks)
        # Roll instances up by root cause so template-wide issues become a single finding.
        groups = self._rollup_findings()
        open_groups = [g for g in groups if g['open']]
        order = list(IssueSeverity)

        # KPI counts are ROOT-CAUSE based (deduped) so repeated template issues don't distort them.
        crit_count = len([g for g in open_groups if g['severity'] == IssueSeverity.CRITICAL])
        serious_count = len([g for g in open_groups if g['severity'] == IssueSeverity.SERIOUS])
        root_cause_count = len(open_groups)
        pages_affected = len({loc for g in open_groups for loc in g['pages']})
        template_issue_count = len([g for g in open_groups if g['page_count'] > 1])
        total_unfixed_groups = max(root_cause_count, 1)
        conformance_label = f"WCAG {self.report.wcag_level_tested.value}"

        # WCAG does not define a numeric compliance score. Report transparent
        # evidence counts instead of an invented conformance-like percentage.
        criteria_flagged = len({
            g['criterion'] for g in open_groups
            if re.match(r'^\d+\.\d+\.\d+$', g['criterion'])
        })

        # WCAG principle breakdown (open root causes)
        principle_map = {"1": "Perceivable", "2": "Operable", "3": "Understandable", "4": "Robust"}
        principles_data = {}
        for p_num, p_name in principle_map.items():
            p_groups = [g for g in open_groups if g['criterion'].startswith(p_num + ".")]
            principles_data[p_name] = {
                "count": len(p_groups),
                "critical": len([g for g in p_groups if g['severity'] == IssueSeverity.CRITICAL]),
                "serious": len([g for g in p_groups if g['severity'] == IssueSeverity.SERIOUS]),
            }

        # Top affected criteria (by number of pages affected)
        crit_pages: Dict[str, set] = {}
        for g in open_groups:
            crit_pages.setdefault(g['criterion'], set()).update(g['pages'])
        top_criteria = sorted(((c, len(p)) for c, p in crit_pages.items()), key=lambda x: -x[1])[:5]

        # Severity distribution as a readable inline bar chart (replaces the cramped pie image)
        sev_color = {IssueSeverity.CRITICAL: "critical", IssueSeverity.SERIOUS: "serious",
                     IssueSeverity.MODERATE: "moderate", IssueSeverity.MINOR: "minor", IssueSeverity.INFO: "informational"}
        sev_counts = {s: len([g for g in open_groups if g['severity'] == s]) for s in IssueSeverity}
        sev_max = max(sev_counts.values()) if any(sev_counts.values()) else 1
        sev_bar_parts = []
        for s in IssueSeverity:
            c = sev_counts[s]
            pct = (c / sev_max) * 100 if sev_max else 0
            sev_bar_parts.append(
                f'<div class="sevbar-row"><span class="sevbar-label">{s.value}</span>'
                f'<div class="sevbar-track"><div class="sevbar-fill {sev_color[s]}" style="width:{pct:.0f}%"></div></div>'
                f'<span class="sevbar-count">{c}</span></div>')
        severity_bars_html = '<div class="sevchart">' + "".join(sev_bar_parts) + '</div>'

        # Broken links collected during the crawl, surfaced like SortSite's "Errors" tab
        broken = self.report.broken_links or {}
        broken_targets = len(broken)
        broken_rows = []
        for target, sources in list(broken.items())[:200]:
            src_list = ", ".join(list(sources)[:5])
            more = f" (+{len(sources) - 5} more)" if len(sources) > 5 else ""
            broken_rows.append(f'<li><code>{_esc(target)}</code> <span class="aff-lines">linked from {len(sources)} page(s): {_esc(src_list)}{more}</span></li>')
        broken_section_html = ""
        broken_pill = '<a href="#broken" class="pill">Broken Links</a>' if broken_rows else ''
        if broken_rows:
            cap = f'<p class="aff-cap">Showing {len(broken_rows)} of {broken_targets} broken targets.</p>' if broken_targets > len(broken_rows) else ''
            broken_section_html = (
                f'<div class="section" id="broken"><div class="section-title">Broken Links ({broken_targets})</div>'
                '<p class="section-sub">Links that returned an HTTP 4xx/5xx error during the crawl. Broken links frustrate every user and are a quality/accessibility defect SortSite also reports.</p>'
                f'{cap}<ul class="affected-list">' + "".join(broken_rows) + '</ul></div>')

        # ── Build grouped finding cards from rolled-up root causes ──
        effort_map = {FixType.AUTOMATIC: ("Auto-Fix", "auto"), FixType.SEMI_AUTOMATIC: ("Guided Fix", "guided"),
                      FixType.MANUAL: ("Manual Fix", "manual")}

        def _reach_text(g):
            if g['page_count'] > 1:
                return f"{g['page_count']} pages &middot; {g['instances']} instances"
            return f"{g['instances']} instance" + ("s" if g['instances'] != 1 else "")

        def _badge_508(crit):
            return '<span class="badge-508" title="WCAG 2.0 A/AA and Section 508 (2017) requirement">508</span>' if is_section_508(crit) else ''

        def _affected_html(g):
            # De-duplicate locations by URL, capping at 200 pages and 5 line numbers per page (SortSite-style).
            by_url: Dict[str, list] = {}
            for loc in g['locations']:
                u = loc.get('url') or 'N/A'
                lst = by_url.setdefault(u, [])
                ln = loc.get('line')
                if ln and len(lst) < 5 and ln not in lst:
                    lst.append(ln)
            urls = list(by_url.keys())
            shown = urls[:200]
            rows = []
            for u in shown:
                lines = by_url[u]
                line_txt = f' <span class="aff-lines">line{"s" if len(lines) > 1 else ""} {", ".join(str(l) for l in lines)}</span>' if lines else ''
                safe = _esc(u)
                link = f'<a href="{_esc(_safe_url(u))}" target="_blank" rel="noopener noreferrer">{safe}</a>' if u.startswith('http') else safe
                rows.append(f'<li>{link}{line_txt}</li>')
            cap_note = f'<p class="aff-cap">Showing {len(shown)} of {g["page_count"]} affected pages.</p>' if g['page_count'] > len(shown) else ''
            return f'<details class="affected"><summary>Affected pages ({g["page_count"]})</summary>{cap_note}<ul class="affected-list">{"".join(rows)}</ul></details>'

        sorted_groups = sorted(groups, key=lambda g: (0 if g['open'] else 1, order.index(g['severity']), -g['page_count'], -g['instances']))

        issues_html_parts = []
        for idx, g in enumerate(sorted_groups):
            sev_class = g['severity'].value.lower()
            effort_label, effort_class = effort_map.get(g['fix_type'], ("Manual Fix", "manual"))
            wcag_url = WCAG_CRITERIA_DATABASE.get(g['criterion'], {}).get('url', '#')
            screenshot_tag = ""
            if g['screenshot_path'] and self.report.screenshot_dir:
                rel_path = Path(self.report.screenshot_dir.name) / Path(g['screenshot_path']).name
                _shot = _esc(rel_path.as_posix())
                screenshot_tag = f'<div class="screenshot"><a href="{_shot}" target="_blank"><img src="{_shot}" alt="Screenshot of element" loading="lazy"/></a></div>'
            tmpl_badge = '<span class="tmpl-badge" title="Appears on multiple pages and is likely a shared template/component">Template &middot; fix once</span>' if g['page_count'] > 1 else ''
            status_badge = '<span class="status-badge unfixed">&#10007; Open</span>' if g['open'] else '<span class="status-badge fixed">&#10003; Fixed</span>'
            rank_badge = f'<span class="rank-badge" title="Priority rank">#{idx + 1}</span>' if (g['open'] and idx < 10) else ''
            issues_html_parts.append(f'''
            <div class="finding {sev_class}" id="grp-{idx}" data-severity="{g['severity'].value}" data-level="{g['level'].value}" data-criterion="{_esc(g['criterion'])}" data-mode="{g['mode'].value}" data-fixed="{'false' if g['open'] else 'true'}" data-principle="{_esc(g['criterion'].split('.')[0])}">
                <div class="finding-header">
                    <div class="finding-header-left">
                        {rank_badge}
                        <span class="sev-badge {sev_class}">{g['severity'].value}</span>
                        <span class="finding-title">{g['criterion']}: {_esc(g['criterion_name'])}</span>
                        <span class="level-badge">Level {g['level'].value}</span>
                        {_badge_508(g['criterion'])}
                    </div>
                    <div class="finding-header-right">
                        <span class="reach-badge">{_reach_text(g)}</span>
                        {tmpl_badge}
                        <span class="effort-badge {effort_class}">{effort_label}</span>
                        {status_badge}
                        <span class="chevron">&#9662;</span>
                    </div>
                </div>
                <div class="finding-detail">
                    <div class="detail-grid">
                        <div class="detail-section whats-wrong">
                            <div class="detail-label">&#9888; What&rsquo;s Wrong</div>
                            <p>{_esc(g['description'])}</p>
                            {f'<p class="location-line"><strong>Selector:</strong> <code>{_esc(g["selector"])}</code></p>' if g['selector'] else ''}
                        </div>
                        <div class="detail-section why-matters">
                            <div class="detail-label">&#128101; Why It Matters</div>
                            <p>{_esc(g['impact'])}</p>
                        </div>
                        <div class="detail-section how-to-fix">
                            <div class="detail-label">&#128295; How to Fix</div>
                            <p>{_esc(g['suggested_fix'])}</p>
                            <a class="wcag-ref" href="{_esc(_safe_url(wcag_url))}" target="_blank" rel="noopener noreferrer">Read WCAG {_esc(g['criterion'])} guidance &rarr;</a>
                        </div>
                    </div>
                    {f'<details class="code-details"><summary>Representative source HTML</summary><pre><code>{_esc(g["element_html"])}</code></pre></details>' if g['element_html'] else ''}
                    {_affected_html(g)}
                    {screenshot_tag}
                </div>
            </div>''')

        issues_html = "\n".join(issues_html_parts)

        # Principle breakdown HTML
        principle_cards = ""
        principle_icons = {"Perceivable": "&#128065;", "Operable": "&#9000;", "Understandable": "&#128218;", "Robust": "&#128736;"}
        for p_name, p_data in principles_data.items():
            p_icon = principle_icons.get(p_name, "")
            bar_pct = (p_data['count'] / total_unfixed_groups) * 100 if total_unfixed_groups else 0
            principle_cards += f'''
            <div class="principle-card">
                <div class="principle-icon">{p_icon}</div>
                <div class="principle-name">{p_name}</div>
                <div class="principle-count">{p_data['count']}</div>
                <div class="principle-bar"><div class="principle-bar-fill" style="width:{bar_pct:.0f}%"></div></div>
                <div class="principle-breakdown">
                    {f'<span class="mini-badge critical">{p_data["critical"]} Critical</span>' if p_data["critical"] else ''}
                    {f'<span class="mini-badge serious">{p_data["serious"]} Serious</span>' if p_data["serious"] else ''}
                </div>
            </div>'''

        # Top criteria HTML
        top_criteria_html = ""
        for crit_id, count in top_criteria:
            details = get_criterion_details(crit_id)
            top_criteria_html += f'<div class="top-crit-row"><span class="top-crit-id">{crit_id}</span><span class="top-crit-name">{details["name"]}</span><span class="top-crit-count">{count}</span></div>'

        # Severity filter options
        sev_options = ''.join([f'<option value="{s.value}">{s.value}</option>' for s in IssueSeverity])
        level_options = ''.join([f'<option value="{l.value}">{l.value}</option>' for l in WCAGLevel])
        mode_options = ''.join([f'<option value="{m.value}">{m.value}</option>' for m in AnalysisMode])

        # Assemble the complete HTML
        # Bandit B608 is excluded in pyproject.toml because this is an offline HTML template, not SQL.
        html_template = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data: file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'">
<title>WCAG Accessibility Audit: {_esc(self.report.target)}</title>
<style>
:root {{
    --bg: #f8f9fc; --surface: #ffffff; --surface-alt: #f1f4f9; --border: #e2e8f0;
    --text: #1e293b; --text-secondary: #64748b; --text-muted: #94a3b8;
    --accent: #3b82f6; --accent-dark: #1e40af;
    --critical: #dc2626; --critical-bg: #fef2f2; --critical-border: #fecaca;
    --serious: #ea580c; --serious-bg: #fff7ed; --serious-border: #fed7aa;
    --moderate: #d97706; --moderate-bg: #fffbeb; --moderate-border: #fde68a;
    --minor: #2563eb; --minor-bg: #eff6ff; --minor-border: #bfdbfe;
    --informational: #7c3aed; --info-bg: #f5f3ff; --info-border: #ddd6fe;
    --success: #16a34a; --success-bg: #f0fdf4;
    --radius: 12px; --radius-sm: 8px; --radius-xs: 6px;
    --shadow: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md: 0 4px 12px rgba(0,0,0,.06), 0 2px 4px rgba(0,0,0,.04);
    --shadow-lg: 0 10px 30px rgba(0,0,0,.08), 0 4px 8px rgba(0,0,0,.04);
    --font: 'Segoe UI', Arial, sans-serif;
    --mono: 'Cascadia Mono', Consolas, monospace;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: var(--font); background: var(--bg); color: var(--text); line-height: 1.6; -webkit-font-smoothing: antialiased; }}
.page {{ max-width: 1280px; margin: 0 auto; padding: 2rem; }}

/* ── Hero ── */
.hero {{ background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #334155 100%); border-radius: var(--radius); padding: 3rem; margin-bottom: 2rem; color: #fff; position: relative; overflow: hidden; }}
.hero::before {{ content: ''; position: absolute; top: -50%; right: -20%; width: 500px; height: 500px; background: radial-gradient(circle, rgba(59,130,246,.15) 0%, transparent 70%); }}
.hero-top {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 2rem; position: relative; z-index: 1; }}
.hero-brand {{ font-size: .85rem; text-transform: uppercase; letter-spacing: .12em; color: rgba(255,255,255,.5); font-weight: 500; }}
.hero-date {{ font-size: .85rem; color: rgba(255,255,255,.5); }}
.hero-title {{ font-size: 1.75rem; font-weight: 700; margin-bottom: .25rem; position: relative; z-index: 1; }}
.hero-target {{ font-size: 1rem; color: rgba(255,255,255,.7); word-break: break-all; position: relative; z-index: 1; }}
.evidence-notice {{ margin: -1rem 0 2rem; padding: 1rem 1.25rem; border: 1px solid #bfdbfe; background: #eff6ff; color: #1e3a8a; border-radius: var(--radius-sm); line-height: 1.55; }}

/* ── KPI Strip ── */
.kpi-strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin: -1.5rem 2rem 2rem; position: relative; z-index: 2; }}
.kpi {{ background: var(--surface); border-radius: var(--radius-sm); padding: 1.25rem 1.5rem; box-shadow: var(--shadow-md); text-align: center; border-top: 3px solid var(--border); }}
.kpi-value {{ font-size: 2rem; font-weight: 700; line-height: 1.1; }}
.kpi-label {{ font-size: .8rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .05em; margin-top: .25rem; }}
.kpi.scope {{ border-top-color: var(--accent); }}
.kpi.critical {{ border-top-color: var(--critical); }}
.kpi.serious {{ border-top-color: var(--serious); }}
.kpi.passed {{ border-top-color: var(--success); }}

/* ── Section ── */
.section {{ margin-bottom: 2rem; }}
.section-title {{ font-size: 1.25rem; font-weight: 700; margin-bottom: 1rem; display: flex; align-items: center; gap: .5rem; }}
.section-title::before {{ content: ''; width: 4px; height: 1.25rem; background: var(--accent); border-radius: 2px; }}

/* ── Overview Grid ── */
.overview-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }}
@media (max-width: 768px) {{ .overview-grid {{ grid-template-columns: 1fr; }} }}
.card {{ background: var(--surface); border-radius: var(--radius-sm); padding: 1.5rem; box-shadow: var(--shadow); }}
.card-title {{ font-size: .95rem; font-weight: 700; margin-bottom: 1rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .04em; }}
.chart-container {{ display: flex; justify-content: center; }}
.chart-container img {{ max-width: 280px; height: auto; }}

/* ── Principle Cards ── */
.principles-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }}
@media (max-width: 768px) {{ .principles-row {{ grid-template-columns: repeat(2, 1fr); }} }}
.principle-card {{ background: var(--surface); border-radius: var(--radius-sm); padding: 1.25rem; box-shadow: var(--shadow); text-align: center; }}
.principle-icon {{ font-size: 1.5rem; margin-bottom: .25rem; }}
.principle-name {{ font-size: .85rem; font-weight: 600; color: var(--text-secondary); margin-bottom: .25rem; }}
.principle-count {{ font-size: 1.75rem; font-weight: 700; }}
.principle-bar {{ height: 4px; background: var(--surface-alt); border-radius: 2px; margin: .5rem 0; }}
.principle-bar-fill {{ height: 100%; background: var(--accent); border-radius: 2px; transition: width .5s; }}
.principle-breakdown {{ display: flex; gap: .35rem; justify-content: center; flex-wrap: wrap; }}
.mini-badge {{ font-size: .7rem; padding: 2px 6px; border-radius: 4px; font-weight: 600; }}
.mini-badge.critical {{ background: var(--critical-bg); color: var(--critical); }}
.mini-badge.serious {{ background: var(--serious-bg); color: var(--serious); }}

/* ── Top Criteria ── */
.top-crit-row {{ display: flex; align-items: center; padding: .6rem 0; border-bottom: 1px solid var(--border); gap: .75rem; }}
.top-crit-row:last-child {{ border-bottom: none; }}
.top-crit-id {{ font-family: var(--mono); font-size: .85rem; font-weight: 500; color: var(--accent-dark); min-width: 3.5rem; }}
.top-crit-name {{ flex: 1; font-size: .9rem; }}
.top-crit-count {{ font-weight: 700; font-size: 1rem; min-width: 2rem; text-align: right; }}

/* ── Filters ── */
.filter-bar {{ background: var(--surface); border-radius: var(--radius-sm); padding: 1rem 1.25rem; box-shadow: var(--shadow); display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; margin-bottom: 1.5rem; }}
.filter-bar label {{ font-size: .8rem; font-weight: 600; color: var(--text-secondary); }}
.filter-bar input, .filter-bar select {{ font-family: var(--font); font-size: .85rem; padding: .45rem .75rem; border: 1px solid var(--border); border-radius: var(--radius-xs); background: var(--surface-alt); color: var(--text); }}
.filter-bar input {{ flex: 1; min-width: 180px; }}
.filter-bar select {{ min-width: 120px; }}

/* ── Sticky toolbar: persistent controls so you never scroll back to navigate/filter ── */
.toolbar {{ position: sticky; top: 0; z-index: 100; display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
    background: rgba(255,255,255,.92); -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
    border: 1px solid var(--border); border-radius: var(--radius-sm); box-shadow: var(--shadow-md);
    padding: .6rem .75rem; margin-bottom: 1.5rem; }}
.toolbar-pills {{ display: flex; gap: .35rem; }}
.pill {{ font-size: .78rem; font-weight: 600; text-decoration: none; color: var(--text-secondary);
    padding: .35rem .7rem; border-radius: 999px; background: var(--surface-alt); white-space: nowrap; }}
.pill:hover {{ color: var(--text); background: #e2e8f0; }}
.toolbar input, .toolbar select {{ font-family: var(--font); font-size: .82rem; padding: .4rem .6rem;
    border: 1px solid var(--border); border-radius: var(--radius-xs); background: var(--surface); color: var(--text); }}
.toolbar input {{ flex: 1; min-width: 150px; }}
.tbtn {{ font-family: var(--font); font-size: .78rem; font-weight: 600; cursor: pointer; color: var(--accent-dark);
    background: var(--surface-alt); border: 1px solid var(--border); border-radius: var(--radius-xs); padding: .4rem .7rem; }}
.tbtn:hover {{ background: #e2e8f0; }}
.result-count {{ font-size: .78rem; color: var(--text-secondary); margin-left: auto; font-weight: 600; white-space: nowrap; }}
.section, .finding {{ scroll-margin-top: 84px; }}
.rank-badge {{ font-size: .68rem; font-weight: 800; padding: 2px 7px; border-radius: 999px; background: #1e293b; color: #fff; }}
.backtop {{ position: fixed; right: 1.25rem; bottom: 1.25rem; z-index: 200; width: 2.75rem; height: 2.75rem;
    border-radius: 50%; border: none; cursor: pointer; background: var(--accent); color: #fff; font-size: 1.15rem;
    box-shadow: var(--shadow-lg); opacity: 0; pointer-events: none; transition: opacity .2s; }}
.backtop.show {{ opacity: 1; pointer-events: auto; }}

/* ── Findings ── */
.finding {{ background: var(--surface); border-radius: var(--radius-sm); box-shadow: var(--shadow); margin-bottom: .75rem; border-left: 5px solid var(--border); overflow: hidden; transition: box-shadow .2s; }}
.finding:hover {{ box-shadow: var(--shadow-md); }}
.finding.critical {{ border-left-color: var(--critical); }}
.finding.serious {{ border-left-color: var(--serious); }}
.finding.moderate {{ border-left-color: var(--moderate); }}
.finding.minor {{ border-left-color: var(--minor); }}
.finding.informational {{ border-left-color: var(--informational); }}

.finding-header {{ display: flex; justify-content: space-between; align-items: center; padding: .85rem 1.25rem; cursor: pointer; user-select: none; gap: .75rem; }}
.finding-header:hover {{ background: var(--surface-alt); }}
.finding-header-left, .finding-header-right {{ display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }}
.finding-header-right {{ flex-shrink: 0; }}

.sev-badge {{ font-size: .7rem; font-weight: 700; padding: 3px 10px; border-radius: 4px; text-transform: uppercase; letter-spacing: .04em; color: #fff; }}
.sev-badge.critical {{ background: var(--critical); }}
.sev-badge.serious {{ background: var(--serious); }}
.sev-badge.moderate {{ background: var(--moderate); }}
.sev-badge.minor {{ background: var(--minor); }}
.sev-badge.informational {{ background: var(--informational); }}
.finding-title {{ font-size: .9rem; font-weight: 600; }}
.level-badge {{ font-size: .7rem; padding: 2px 8px; border-radius: 4px; background: var(--surface-alt); color: var(--text-secondary); font-weight: 600; }}
.effort-badge {{ font-size: .7rem; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
.effort-badge.auto {{ background: #dcfce7; color: #166534; }}
.effort-badge.guided {{ background: #dbeafe; color: #1e40af; }}
.effort-badge.manual {{ background: #f1f5f9; color: #475569; }}
.status-badge {{ font-size: .7rem; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
.status-badge.fixed {{ background: var(--success-bg); color: var(--success); }}
.status-badge.unfixed {{ background: var(--critical-bg); color: var(--critical); }}
.chevron {{ font-size: .75rem; color: var(--text-muted); transition: transform .2s; }}
.finding.open .chevron {{ transform: rotate(180deg); }}

.finding-detail {{ display: none; padding: 0 1.25rem 1.25rem; }}
.finding.open .finding-detail {{ display: block; }}
.detail-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; margin-top: .5rem; }}
@media (max-width: 900px) {{ .detail-grid {{ grid-template-columns: 1fr; }} }}
.detail-section {{ background: var(--surface-alt); border-radius: var(--radius-xs); padding: 1rem; }}
.detail-section.whats-wrong {{ border-left: 3px solid var(--critical); }}
.detail-section.why-matters {{ border-left: 3px solid var(--serious); }}
.detail-section.how-to-fix {{ border-left: 3px solid var(--success); }}
.detail-label {{ font-size: .8rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--text-secondary); margin-bottom: .5rem; }}
.detail-section p {{ font-size: .88rem; color: var(--text); margin-bottom: .5rem; }}
.location-line {{ font-size: .82rem !important; color: var(--text-secondary) !important; }}
.location-line code {{ font-family: var(--mono); font-size: .8rem; background: var(--surface); padding: 1px 5px; border-radius: 3px; }}
.fix-applied {{ background: var(--success-bg); padding: .5rem .75rem; border-radius: var(--radius-xs); font-size: .85rem; }}
.wcag-ref {{ display: inline-block; font-size: .82rem; color: var(--accent); text-decoration: none; font-weight: 600; margin-top: .25rem; }}
.wcag-ref:hover {{ text-decoration: underline; }}

.code-details {{ margin-top: .75rem; }}
.code-details summary {{ font-size: .82rem; color: var(--accent); cursor: pointer; font-weight: 600; }}
.code-details pre {{ background: #1e293b; color: #e2e8f0; padding: 1rem; border-radius: var(--radius-xs); overflow-x: auto; font-size: .8rem; line-height: 1.5; margin-top: .5rem; }}
.code-details code {{ font-family: var(--mono); }}
.screenshot {{ margin-top: .75rem; text-align: center; }}
.screenshot img {{ max-width: 100%; max-height: 300px; border-radius: var(--radius-xs); border: 1px solid var(--border); }}

/* ── 508 / reach / template badges ── */
.badge-508 {{ font-size: .62rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: #0f766e; color: #fff; letter-spacing: .03em; }}
.reach-badge {{ font-size: .7rem; padding: 2px 8px; border-radius: 4px; font-weight: 600; background: #eef2ff; color: #3730a3; white-space: nowrap; }}
.tmpl-badge {{ font-size: .68rem; padding: 2px 8px; border-radius: 4px; font-weight: 700; background: #fef3c7; color: #92400e; white-space: nowrap; }}

/* ── Section sub-text ── */
.section-sub {{ font-size: .9rem; color: var(--text-secondary); margin-bottom: 1rem; max-width: 70ch; }}

/* ── Priority remediation plan ── */
.remediation {{ display: flex; flex-direction: column; gap: .5rem; }}
.plan-row {{ display: grid; grid-template-columns: 2rem 5.5rem 5.5rem 1fr auto; align-items: center; gap: .75rem; padding: .65rem .9rem; background: var(--surface); border-radius: var(--radius-xs); box-shadow: var(--shadow); text-decoration: none; color: var(--text); border-left: 4px solid var(--border); transition: box-shadow .15s, transform .05s; }}
.plan-row:hover {{ box-shadow: var(--shadow-md); transform: translateX(2px); }}
.plan-row.critical {{ border-left-color: var(--critical); }}
.plan-row.serious {{ border-left-color: var(--serious); }}
.plan-row.moderate {{ border-left-color: var(--moderate); }}
.plan-row.minor {{ border-left-color: var(--minor); }}
.plan-row.informational {{ border-left-color: var(--informational); }}
.plan-rank {{ font-weight: 700; color: var(--text-muted); font-size: 1rem; text-align: center; }}
.plan-crit {{ font-family: var(--mono); font-size: .78rem; font-weight: 500; color: var(--accent-dark); display: flex; align-items: center; gap: .35rem; }}
.plan-title {{ font-size: .88rem; }}
.plan-fix {{ color: var(--text-secondary); }}
.plan-reach {{ font-size: .75rem; font-weight: 600; color: #3730a3; background: #eef2ff; padding: 2px 8px; border-radius: 4px; white-space: nowrap; }}
@media (max-width: 768px) {{ .plan-row {{ grid-template-columns: 1.5rem auto 1fr; }} .plan-crit, .plan-reach {{ display: none; }} }}

/* ── Affected pages list ── */
.affected {{ margin-top: .75rem; }}
.affected summary {{ font-size: .82rem; color: var(--accent); cursor: pointer; font-weight: 600; }}
.affected-list {{ list-style: none; margin: .5rem 0 0; padding: .5rem .75rem; background: var(--surface-alt); border-radius: var(--radius-xs); max-height: 320px; overflow-y: auto; }}
.affected-list li {{ font-size: .8rem; padding: .25rem 0; border-bottom: 1px solid var(--border); word-break: break-all; }}
.affected-list li:last-child {{ border-bottom: none; }}
.aff-lines {{ font-family: var(--mono); font-size: .72rem; color: var(--text-muted); }}
.aff-cap {{ font-size: .76rem; color: var(--text-muted); margin-bottom: .35rem; }}

/* ── Severity bar chart (replaces cramped pie) ── */
.sevchart {{ display: flex; flex-direction: column; gap: .65rem; padding-top: .25rem; }}
.sevbar-row {{ display: grid; grid-template-columns: 7rem 1fr 2.5rem; align-items: center; gap: .85rem; }}
.sevbar-label {{ font-size: .92rem; font-weight: 600; }}
.sevbar-track {{ background: var(--surface-alt); border-radius: 6px; height: 1.6rem; overflow: hidden; }}
.sevbar-fill {{ height: 100%; border-radius: 6px; min-width: 3px; transition: width .5s ease; }}
.sevbar-fill.critical {{ background: var(--critical); }}
.sevbar-fill.serious {{ background: var(--serious); }}
.sevbar-fill.moderate {{ background: var(--moderate); }}
.sevbar-fill.minor {{ background: var(--minor); }}
.sevbar-fill.informational {{ background: var(--informational); }}
.sevbar-count {{ font-size: 1.15rem; font-weight: 700; text-align: right; }}

/* ── Footer ── */
.report-footer {{ text-align: center; padding: 2rem 0 1rem; color: var(--text-muted); font-size: .8rem; }}
.no-chart {{ color: var(--text-muted); font-style: italic; }}

/* ── Print ── */
@media print {{
    body {{ background: #fff; }}
    .page {{ padding: 0; }}
    .filter-bar, .toolbar, .backtop {{ display: none; }}
    .finding {{ break-inside: avoid; }}
    .finding-detail {{ display: block !important; }}
    .affected, .code-details {{ break-inside: avoid; }}
    .hero {{ background: #1e293b !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
</style>
</head>
<body>
<div class="page">

    <!-- ═══ HERO ═══ -->
    <div class="hero">
        <div class="hero-top">
            <div class="hero-brand">WCAG 2.2 Site and PDF Scanner: Accessibility Audit</div>
            <div class="hero-date">{self.report.timestamp[:10]}</div>
        </div>
        <div class="hero-title">Accessibility Audit Report</div>
        <div class="hero-target">{_esc(self.report.target)}</div>
    </div>

    <div class="evidence-notice"><strong>Automated evidence, not a conformance determination.</strong>
    Passing a narrow automated check does not establish that an entire WCAG success criterion passes.
    Manual evaluation with assistive technology and representative users remains necessary.</div>

    <!-- ═══ KPI STRIP ═══ -->
    <div class="kpi-strip">
        <div class="kpi scope">
            <div class="kpi-value" style="color:var(--accent)">{criteria_flagged}</div>
            <div class="kpi-label">Criteria flagged</div>
        </div>
        <div class="kpi critical">
            <div class="kpi-value" style="color:var(--critical)">{crit_count}</div>
            <div class="kpi-label">Critical</div>
        </div>
        <div class="kpi serious">
            <div class="kpi-value" style="color:var(--serious)">{serious_count}</div>
            <div class="kpi-label">Serious</div>
        </div>
        <div class="kpi">
            <div class="kpi-value">{root_cause_count}</div>
            <div class="kpi-label">Open Root Causes</div>
        </div>
        <div class="kpi">
            <div class="kpi-value">{pages_affected}</div>
            <div class="kpi-label">Pages Affected</div>
        </div>
        <div class="kpi passed">
            <div class="kpi-value" style="color:var(--success)">{total_passed}</div>
            <div class="kpi-label">No failure detected</div>
        </div>
    </div>

    <!-- Sticky toolbar with persistent controls -->
    <div class="toolbar">
        <div class="toolbar-pills">
            <a href="#summary" class="pill">Summary</a>
            <a href="#findings" class="pill">Findings</a>
            {broken_pill}
        </div>
        <input type="text" id="fText" placeholder="Search findings…">
        <select id="fSev"><option value="">Severity</option>{sev_options}</select>
        <select id="fLevel"><option value="">Level</option>{level_options}</select>
        <select id="fStatus"><option value="">Status</option><option value="false">Open</option><option value="true">Fixed</option></select>
        <select id="fPrinciple"><option value="">Principle</option><option value="1">Perceivable</option><option value="2">Operable</option><option value="3">Understandable</option><option value="4">Robust</option></select>
        <select id="fMode"><option value="">Source</option>{mode_options}</select>
        <button class="tbtn" id="expandAll">Expand all</button>
        <button class="tbtn" id="collapseAll">Collapse all</button>
        <span class="result-count" id="resultCount"></span>
    </div>

    <!-- ═══ OVERVIEW ═══ -->
    <div class="section" id="summary">
        <div class="section-title">Executive Overview</div>
        <div class="overview-grid">
            <div class="card">
                <div class="card-title">Severity Distribution: Open Root Causes</div>
                {severity_bars_html}
            </div>
            <div class="card">
                <div class="card-title">Most Affected Criteria</div>
                {top_criteria_html if top_criteria_html else '<p class="no-chart">No issues found.</p>'}
            </div>
        </div>
    </div>

    <!-- ═══ PRINCIPLES BREAKDOWN ═══ -->
    <div class="section">
        <div class="section-title">WCAG Principles Breakdown</div>
        <div class="principles-row">
            {principle_cards}
        </div>
    </div>

    <!-- Findings in priority order, expanded in place -->
    <div class="section" id="findings">
        <div class="section-title">Findings: Priority Order</div>
        <p class="section-sub">{root_cause_count} root-cause issue(s) across {pages_affected} page(s) ({total_issues} total instances). <strong>{template_issue_count}</strong> are template-level; fix once, resolved everywhere. Listed worst-first (top 10 ranked); expand any item in place, or use <strong>Expand all</strong> in the toolbar above. Automated target: <strong>{conformance_label}</strong> &middot; Section 508 (2017).</p>
        <div id="findings-list">
            {issues_html}
        </div>
    </div>

    {broken_section_html}

    <button class="backtop" id="backTop" title="Back to top" aria-label="Back to top">&#8593;</button>

    <div class="report-footer">
        Generated by {APP_NAME} v{APP_VERSION} &middot; WCAG {self.report.wcag_level_tested.value} automated target &middot; Analysis took {self.report.analysis_duration:.1f}s
    </div>
</div>

<script>
document.querySelectorAll('.finding-header').forEach(h => {{
    h.addEventListener('click', () => h.parentElement.classList.toggle('open'));
}});

const fText = document.getElementById('fText');
const fSev = document.getElementById('fSev');
const fLevel = document.getElementById('fLevel');
const fMode = document.getElementById('fMode');
const fStatus = document.getElementById('fStatus');
const fPrinciple = document.getElementById('fPrinciple');

const resultCount = document.getElementById('resultCount');
function filterFindings() {{
    const text = fText.value.toLowerCase();
    let shown = 0, total = 0;
    document.querySelectorAll('.finding').forEach(f => {{
        total++;
        const show =
            (text === '' || f.innerText.toLowerCase().includes(text)) &&
            (fSev.value === '' || f.dataset.severity === fSev.value) &&
            (fLevel.value === '' || f.dataset.level === fLevel.value) &&
            (fMode.value === '' || f.dataset.mode === fMode.value) &&
            (fStatus.value === '' || f.dataset.fixed === fStatus.value) &&
            (fPrinciple.value === '' || f.dataset.principle === fPrinciple.value);
        f.style.display = show ? '' : 'none';
        if (show) shown++;
    }});
    if (resultCount) resultCount.textContent = shown + ' of ' + total + ' shown';
}}

[fText, fSev, fLevel, fMode, fStatus, fPrinciple].forEach(el => {{
    el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', filterFindings);
}});
filterFindings();

// Expand or collapse all to read the report linearly
const expandAllBtn = document.getElementById('expandAll');
const collapseAllBtn = document.getElementById('collapseAll');
if (expandAllBtn) expandAllBtn.addEventListener('click', () =>
    document.querySelectorAll('.finding').forEach(f => {{ if (f.style.display !== 'none') f.classList.add('open'); }}));
if (collapseAllBtn) collapseAllBtn.addEventListener('click', () =>
    document.querySelectorAll('.finding').forEach(f => f.classList.remove('open')));

// Back-to-top floating button
const backTopBtn = document.getElementById('backTop');
if (backTopBtn) {{
    window.addEventListener('scroll', () => backTopBtn.classList.toggle('show', window.scrollY > 600));
    backTopBtn.addEventListener('click', () => window.scrollTo({{top: 0, behavior: 'smooth'}}));
}}

// Expand and reveal a finding when jumped to from the remediation plan
function openFromHash() {{
    if (location.hash && location.hash.indexOf('#grp-') === 0) {{
        const el = document.querySelector(location.hash);
        if (el) {{ el.classList.add('open'); el.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
    }}
}}
document.querySelectorAll('.plan-row').forEach(r => r.addEventListener('click', () => setTimeout(openFromHash, 0)));
window.addEventListener('hashchange', openFromHash);
openFromHash();
</script>
</body>
</html>'''

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html_template)
            console.print(f"✓ Interactive HTML report saved to [cyan]{path}[/cyan]")
        except Exception as e:
            raise RuntimeError(f"HTML report generation failed: {e}") from e


    def generate_pdf(self):
        """Generate PDF report using FPDF."""
        if not FPDF:
            console.print("[yellow]Skipping PDF report generation (fpdf2 not available)[/yellow]")
            return

        path = self.output_dir / "report.pdf"

        class PDF(FPDF):
            @staticmethod
            def safe_text(value):
                """Keep built-in-font output valid for arbitrary paths and URLs."""
                return str(value).encode('latin-1', errors='replace').decode('latin-1')

            def header(self):
                self.set_font('Helvetica', 'B', 12)
                self.cell(
                    w=0,
                    h=10,
                    text=self.safe_text(f'{APP_NAME} Accessibility Report'),
                    border=0,
                    align='C',
                    new_x=XPos.LMARGIN,
                    new_y=YPos.NEXT,
                )
                self.ln(5)

            def footer(self):
                self.set_y(-15)
                self.set_font('Helvetica', 'I', 8)
                self.cell(
                    w=0,
                    h=10,
                    text=f'Page {self.page_no()}/{{nb}}',
                    border=0,
                    align='C',
                    new_x=XPos.RIGHT,
                    new_y=YPos.TOP,
                )

            def chapter_title(self, title):
                self.set_font('Helvetica', 'B', 14)
                self.cell(
                    w=0,
                    h=10,
                    text=title,
                    border=0,
                    align='L',
                    new_x=XPos.LMARGIN,
                    new_y=YPos.NEXT,
                )
                self.ln(4)

            def chapter_body(self, body):
                self.set_font('Helvetica', '', 10)
                self.set_x(self.l_margin)
                self.multi_cell(0, 6, self.safe_text(body))
                self.set_x(self.l_margin)
                self.ln()

            def summary_line(self, text, align='L'):
                self.set_font('Helvetica', '', 10)
                self.set_x(self.l_margin)
                self.multi_cell(0, 6, self.safe_text(text), align=align)
                self.set_x(self.l_margin)

        pdf = PDF()
        pdf.alias_nb_pages()
        pdf.add_page()

        # Title Page
        pdf.set_font('Helvetica', 'B', 20)
        pdf.cell(
            w=0,
            h=15,
            text='Accessibility Audit Report',
            border=0,
            align='C',
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font('Helvetica', '', 16)
        pdf.cell(
            w=0,
            h=10,
            text=APP_NAME,
            border=0,
            align='C',
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font('Helvetica', '', 14)
        pdf.ln(10)
        pdf.summary_line('Target: ' + self.report.target, 'C')
        pdf.summary_line('Generated: ' + self.report.timestamp, 'C')
        pdf.ln(20)

        # Summary
        pdf.chapter_title("Summary")
        pdf.summary_line(f"WCAG Level Tested: {self.report.wcag_level_tested.value}")
        pdf.summary_line(f"Analysis Duration: {self.report.analysis_duration:.2f} seconds")
        pdf.summary_line(f"Total URLs Crawled: {self.report.summary.get('total_urls_crawled', 0)}")
        pdf.summary_line(f"Total Files Analyzed: {self.report.summary.get('total_files_analyzed', 0)}")
        pdf.summary_line(f"Total Issues Found: {self.report.summary.get('total_issues', 0)}")
        pdf.summary_line(f"Issues Fixed: {self.report.summary.get('issues_fixed', 0)}")
        pdf.summary_line(f"Narrow checks with no detected failure: {self.report.summary.get('total_passed_checks', 0)}")
        pdf.summary_line(f"Total Broken Links: {self.report.summary.get('total_broken_links', 0)}")
        pdf.ln(5)

        # Issues by severity
        pdf.set_font('Helvetica', 'B', 12)
        pdf.cell(
            w=0,
            h=8,
            text="Issues by Severity:",
            border=0,
            align='L',
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font('Helvetica', '', 10)
        for sub_key, sub_val in self.report.summary['issues_by_severity'].items():
            pdf.cell(
                w=0,
                h=6,
                text=f"  {sub_key}: {sub_val}",
                border=0,
                align='L',
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        pdf.ln(5)

        try:
            pdf.output(str(path))
            console.print(f"✓ PDF report saved to [cyan]{path}[/cyan]")
        except Exception as e:
            raise RuntimeError(f"PDF report generation failed: {e}") from e

    def generate_junit_xml(self):
        """Generate JUnit XML formatted report."""
        path = self.output_dir / "report.xml"

        def clean_xml_text(value: Any) -> str:
            text = "" if value is None else str(value)
            return ''.join(
                character if (
                    character in {'\t', '\n', '\r'}
                    or 0x20 <= ord(character) <= 0xD7FF
                    or 0xE000 <= ord(character) <= 0xFFFD
                    or 0x10000 <= ord(character) <= 0x10FFFF
                ) else '\uFFFD'
                for character in text
            )

        def escape_xml(value: Any) -> str:
            return html_lib.escape(clean_xml_text(value), quote=True).replace('&#x27;', '&apos;')

        testsuite_name = f"WCAG Accessibility Scan - {self.report.target}"

        total_tests = len(self.report.issues) + len(self.report.passed_checks)
        failures = len([i for i in self.report.issues if not i.fixed])
        errors = 0  # Reserve for actual runtime errors, not fixed issues
        skipped = 0

        xml_output = []
        xml_output.append('<?xml version="1.0" encoding="UTF-8"?>')
        xml_output.append(
            f'<testsuites time="{self.report.analysis_duration:.2f}" tests="{total_tests}" '
            f'failures="{failures}" errors="{errors}" skipped="{skipped}">'
        )
        xml_output.append(f'  <testsuite name="{escape_xml(testsuite_name)}" '
                          f'timestamp="{escape_xml(self.report.timestamp)}" '
                          f'time="{self.report.analysis_duration:.2f}" '
                          f'tests="{total_tests}" failures="{failures}" errors="{errors}" skipped="{skipped}">'
        )

        # Issues as test cases
        for issue in self.report.issues:
            testcase_name = f"{issue.criterion} - {issue.criterion_name} ({issue.severity.value})"
            class_name = issue.url or issue.file_path or "unknown_location"

            xml_output.append(f'    <testcase name="{escape_xml(testcase_name)}" classname="{escape_xml(class_name)}" time="0.0">')
            if not issue.fixed:
                xml_output.append(f'      <failure message="{escape_xml(issue.description)}" type="{issue.severity.value}">')
                details = [
                    f'Criterion: {issue.criterion} - {issue.criterion_name}',
                    f'Level: {issue.level.value}',
                    f'Severity: {issue.severity.value}',
                    f'Mode: {issue.mode.value}',
                    f'Impact: {issue.impact}',
                    f'Location: {issue.url or issue.file_path}{f" (Line: {issue.line_number})" if issue.line_number else ""}',
                ]
                if issue.selector:
                    details.append(f'Selector: {issue.selector}')
                if issue.element_html:
                    details.append(f'Element HTML: {issue.element_html}')
                details.append(f'Suggested Fix: {issue.suggested_fix}')
                if issue.screenshot_path:
                    details.append(f'Screenshot: {issue.screenshot_path}')
                xml_output.append(escape_xml('\n'.join(details)))
                xml_output.append('      </failure>')
            else:
                # Fixed issues are passing tests with system-out for traceability
                xml_output.append('      <system-out>')
                fixed_details = [
                    f'FIXED: {issue.criterion} - {issue.criterion_name}',
                    f'Severity: {issue.severity.value}',
                    f'Fix Applied: {issue.fix_applied}',
                    f'Location: {issue.url or issue.file_path}{f" (Line: {issue.line_number})" if issue.line_number else ""}',
                ]
                xml_output.append(escape_xml('\n'.join(fixed_details)))
                xml_output.append('      </system-out>')

            xml_output.append('    </testcase>')

        # Passed checks as successful test cases
        for passed_check in self.report.passed_checks:
            testcase_name = f"No detected failure: {passed_check.criterion} - {passed_check.criterion_name}"
            class_name = passed_check.url or passed_check.file_path or "unknown_location"
            xml_output.append(f'    <testcase name="{escape_xml(testcase_name)}" classname="{escape_xml(class_name)}" time="0.0"/>')

        xml_output.append('  </testsuite>')
        xml_output.append('</testsuites>')

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(xml_output))
            console.print(f"✓ JUnit XML report saved to [cyan]{path}[/cyan]")
        except Exception as e:
            raise RuntimeError(f"JUnit report generation failed: {e}") from e


# ==============================================================================
# MAIN CONTROLLER
# ==============================================================================

class A11yPowerTool:
    """The main controller class for the entire application."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        global _AXE_LOCAL_OVERRIDE, _AXE_SOURCE_CACHE
        requested_axe_override = config.get('axe_script') or None
        if requested_axe_override != _AXE_LOCAL_OVERRIDE:
            _AXE_LOCAL_OVERRIDE = requested_axe_override
            _AXE_SOURCE_CACHE = None
        self.report_output_dir = Path(config.get('output_dir', 'a11y_reports'))
        self.report_output_dir.mkdir(parents=True, exist_ok=True)
        _install_quiet_logging(self.report_output_dir)

        # Create a dedicated directory for screenshots within the report output
        self.screenshot_output_dir = self.report_output_dir / "screenshots"
        self.screenshot_output_dir.mkdir(parents=True, exist_ok=True)

        self.report = AccessibilityReport(
            target=config.get('target', ''),
            wcag_level_tested=WCAGLevel(config.get('level', 'AA').upper()),
            screenshot_dir=self.screenshot_output_dir
        )

        # Analyzers are instantiated per page inside _analyze_page_content so that many pages
        # can be analyzed concurrently without sharing mutable per-page state.
        self._level = self.report.wcag_level_tested

        # Cross-page consistency (3.2.3/3.2.4/3.2.6) accumulator + bounded sampling budget.
        self._consistency = ConsistencyAccumulator()
        self._consistency_budget = int(config.get('consistency_sample') or 1000)

        self.playwright_instance = None
        self.browser: Optional[Browser] = None

    async def _browser_route_guard(self, route):
        """Block unsafe browser requests, including hostile subresources."""
        request_url = route.request.url
        parsed = urlparse(request_url)
        if parsed.scheme in {"about", "blob", "data"}:
            await route.continue_()
            return
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            logger.warning("Blocked browser request with unsafe URL: %s", request_url)
            await route.abort("blockedbyclient")
            return

        hostname = parsed.hostname or ""
        try:
            literal_ip = ipaddress.ip_address(hostname)
            addresses = [str(literal_ip)]
        except ValueError:
            try:
                default_port = 443 if parsed.scheme == "https" else 80
                infos = await asyncio.wait_for(
                    asyncio.to_thread(socket.getaddrinfo, hostname, parsed.port or default_port),
                    timeout=5,
                )
                addresses = sorted({info[4][0] for info in infos})
            except Exception as exc:
                logger.warning("Blocked browser request after DNS failure for %s: %s", hostname, exc)
                await route.abort("failed")
                return

        if any(_ip_is_blocked(address, self.config.get('allow_private_hosts', False)) for address in addresses):
            logger.warning("Blocked browser request to non-public host: %s", hostname)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _new_secure_browser_context(self):
        """Create a browser context with defensive defaults for untrusted pages."""
        if not self.browser:
            return None
        context = await self.browser.new_context(
            user_agent=self.config['user_agent'],
            accept_downloads=False,
            service_workers="block",
        )
        await context.clear_permissions()
        await context.route("**/*", self._browser_route_guard)
        return context

    async def _initialize_browser(self):
        """Initializes Playwright browser instance."""
        if self.browser:
            return
        try:
            if not PLAYWRIGHT_READY or async_playwright is None:
                raise PlaywrightError("Playwright is not installed")
            self.playwright_instance = await async_playwright().__aenter__()
            launch_args = ['--disable-gpu']
            if self.config.get('no_sandbox'):
                launch_args += ['--no-sandbox', '--disable-setuid-sandbox']
                logger.warning("Chromium sandbox disabled via --no-sandbox. Use only in a trusted/"
                               "containerized environment; it weakens isolation when rendering untrusted pages.")
            self.browser = await self.playwright_instance.chromium.launch(
                headless=not self.config.get('no_headless'),
                args=launch_args,
            )
            logger.info("Playwright browser launched.")
        except PlaywrightError as pe:
            console.print(f"[bold red]Error launching Playwright browser: {pe}[/bold red]")
            console.print("[yellow]Please ensure `playwright install` has been run and Playwright OS dependencies (`playwright install-deps`) are met.[/yellow]")
            self.browser = None
            self.playwright_instance = None
            raise

    async def _close_browser(self):
        """Closes Playwright browser instance."""
        if self.browser:
            await self.browser.close()
            self.browser = None
            logger.info("Playwright browser closed.")
        if self.playwright_instance:
            await self.playwright_instance.stop()
            self.playwright_instance = None
            logger.info("Playwright instance exited.")

    async def run(self):
        """Main execution method."""
        start_time = time.time()
        target = self.config['target']
        is_url_target = target.startswith('http')
        is_path_like = Path(target).exists()

        # Determine which analysis paths to take
        can_run_dynamic_analysis = False
        if is_url_target:
            try:
                await self._initialize_browser()
                can_run_dynamic_analysis = True
            except Exception:
                console.print("[bold yellow]Dynamic analysis (Playwright/Axe) will be skipped due to browser launch failure.[/bold yellow]")

        elif is_path_like:
            if Path(target).is_dir():
                await self._run_for_local_dir(target)
            elif Path(target).is_file():
                await self._run_for_local_file(target)
            else:
                console.print(f"[bold red]Error: Target '{target}' is not a valid URL or local file/directory.[/bold red]")
                return
            console.print("[yellow]Dynamic analysis (Playwright/Axe) is generally skipped for local file:// targets due to browser security restrictions (CORS).[/yellow]")
            console.print("[yellow]To enable dynamic analysis for local files, consider serving them via a local HTTP server.[/yellow]")
            can_run_dynamic_analysis = False

        else:
            console.print(f"[bold red]Error: Target '{target}' is not a valid URL or existing local file/directory.[/bold red]")
            return

        # If dynamic capabilities are available and target is a URL
        if can_run_dynamic_analysis and is_url_target:
            await self._run_for_url(target)
        elif is_url_target and not can_run_dynamic_analysis:
            # Fallback: fetch HTML via aiohttp and run static analysis only
            console.print("[yellow]Falling back to static-only analysis for URL target (no browser available).[/yellow]")
            try:
                async with aiohttp.ClientSession(connector=_safe_connector(self.config.get('allow_private_hosts', False))) as session:
                    async with _safe_get(
                        session, target,
                        allow_private=self.config.get('allow_private_hosts', False),
                        timeout=aiohttp.ClientTimeout(total=30),
                        headers={'User-Agent': self.config.get('user_agent', DEFAULT_USER_AGENT)},
                    ) as resp:
                        expected_host = (urlparse(target).hostname or '').lower().rstrip('.')
                        if not _same_host_family(str(resp.url), expected_host, True):
                            raise ValueError("Target redirected outside its hostname family")
                        if resp.status == 200:
                            html_content = await _read_capped_text(resp)
                            self.report.all_urls_crawled.add(target)
                            await self._analyze_page_content(html_content, target, page=None)
                        else:
                            console.print(f"[bold red]HTTP {resp.status} when fetching {target}[/bold red]")
            except Exception as e:
                console.print(f"[bold red]Failed to fetch URL for static analysis: {e}[/bold red]")

        # Ensure browser is closed if it was launched
        if self.browser:
            await self._close_browser()

        self.report.analysis_duration = time.time() - start_time
        self.report.compile_summary()

        console.print(Panel(f"[bold green]Analysis Complete![/bold green] Duration: {self.report.analysis_duration:.2f}s. Found [bold red]{len(self.report.issues)}[/bold red] issues; [bold blue]{len(self.report.passed_checks)}[/bold blue] narrow checks found no failure.",
                            title="Finished", border_style="green"))

        if self.config.get('fix'):
            local_issues_to_fix = [i for i in self.report.issues if i.file_path and i.fix_type in [FixType.SEMI_AUTOMATIC, FixType.AUTOMATIC] and not i.fixed]
            if local_issues_to_fix:
                fixer_tui = FixerTUI(self.report)
                await fixer_tui.run()
                self.report.compile_summary()
                console.print(Panel(f"[bold green]Post-Fixing Summary:[/bold green] [bold red]{len([i for i in self.report.issues if not i.fixed])}[/bold red] issues remain unfixed. [bold green]{len([i for i in self.report.issues if i.fixed])}[/bold green] issues fixed.",
                                   border_style="green"))
            else:
                console.print("[yellow]No local files with automatic or semi-automatic fixes pending review.[/yellow]")

        if self.config.get('report_formats'):
            reporter = ReportGenerator(self.report, self.report_output_dir)
            reporter.generate_all(self.config['report_formats'].split(','))

        if self.config.get('ci_mode'):
            unfixed_critical_serious = [i for i in self.report.issues if i.severity in [IssueSeverity.CRITICAL, IssueSeverity.SERIOUS] and not i.fixed]
            if unfixed_critical_serious:
                console.print(f"[bold red]CI Mode Failure: {len(unfixed_critical_serious)} unfixed critical or serious issues found. Exiting with error code 1.[/bold red]")
                sys.exit(1)
            else:
                console.print("[bold green]CI Mode Success: No unfixed critical or serious issues found.[/bold green]")

    async def _fetch_text(self, url: str, session: Optional[aiohttp.ClientSession]) -> str:
        """Fetch text via the shared session if provided, else a one-off SSRF-guarded session. Size-capped (F6)."""
        timeout = aiohttp.ClientTimeout(total=20)
        allow_private = self.config.get('allow_private_hosts', False)
        if session is not None:
            async with _safe_get(session, url, allow_private=allow_private, timeout=timeout) as resp:
                return await _read_capped_text(resp) if resp.status == 200 else ""
        async with aiohttp.ClientSession(connector=_safe_connector(allow_private)) as s:
            async with _safe_get(s, url, allow_private=allow_private, timeout=timeout) as resp:
                return await _read_capped_text(resp) if resp.status == 200 else ""

    async def _analyze_page_content(self, html_content: str, url_or_path: str, page: Optional = None,
                                    session: Optional[aiohttp.ClientSession] = None,
                                    collect_fingerprint: bool = False):
        """Orchestrates analysis of a single page/file.

        Uses per-call analyzer instances (not shared self.* analyzers) so it is safe to run
        concurrently across many pages. The HTML is parsed once and reused.
        """
        is_url = url_or_path.startswith('http')
        current_file_path = None if is_url else url_or_path
        current_url = url_or_path if is_url else None
        level = self._level
        soup = BeautifulSoup(html_content, 'lxml')

        # Static analysis
        static_analyzer = StaticAnalyzer(level)
        static_analyzer.file_path = current_file_path
        static_analyzer.url = current_url
        s_issues, s_passed = static_analyzer.analyze(html_content, url_or_path)
        self.report.issues.extend(s_issues)
        self.report.passed_checks.extend(s_passed)

        # HTML validation is a non-WCAG quality check and runs on every page when enabled
        if self.config.get('validate_html', True):
            html_validator = HTMLValidationAnalyzer(level)
            html_validator.file_path = current_file_path
            html_validator.url = current_url
            v_issues, v_passed = html_validator.analyze(html_content, url_or_path, soup=soup)
            self.report.issues.extend(v_issues)
            self.report.passed_checks.extend(v_passed)

        # Spelling is optional and needs pyspellchecker
        if self.config.get('spell_check', True):
            spell_analyzer = SpellingAnalyzer(level, lang=self.config.get('spell_lang', 'en'),
                                              ignore_caps=not self.config.get('spell_include_caps', False))
            spell_analyzer.file_path = current_file_path
            spell_analyzer.url = current_url
            sp_issues, sp_passed = spell_analyzer.analyze(soup, url_or_path)
            self.report.issues.extend(sp_issues)
            self.report.passed_checks.extend(sp_passed)

        # Content (NLP) analysis
        content_analyzer = ContentAnalyzer(level)
        content_analyzer.file_path = current_file_path
        content_analyzer.url = current_url
        c_issues, c_passed = content_analyzer.analyze(BeautifulSoup(html_content, 'lxml'), url_or_path)
        self.report.issues.extend(c_issues)
        self.report.passed_checks.extend(c_passed)

        # CSS gathering covers inline, style blocks, and deduplicated external files
        css_contents_to_analyze: Dict[str, Dict[str, str]] = {}
        for i, el in enumerate(soup.find_all(attrs={"style": True})):
            if el.get('style'):
                element_id_or_tag = el.get('id', el.name)
                css_contents_to_analyze[f"{url_or_path}#inline-style-{element_id_or_tag}-{i}"] = {'content': el['style'], 'selector': element_id_or_tag}
        for i, style_tag in enumerate(soup.find_all('style')):
            if style_tag.string and style_tag.string.strip():
                css_contents_to_analyze[f"{url_or_path}#style-block-{i}"] = {'content': style_tag.string, 'selector': f"style-block-{i}"}
        for link_tag in soup.find_all('link', rel='stylesheet', href=True):
            css_href = link_tag['href']
            absolute_css_url = urljoin(url_or_path, css_href)
            if absolute_css_url in self.report.all_files_analyzed:
                continue
            # Claim early so concurrent pages don't all fetch the same stylesheet.
            self.report.all_files_analyzed.add(absolute_css_url)
            try:
                if is_url:
                    css_text = await self._fetch_text(absolute_css_url, session)
                else:
                    local_css_path = _uri_to_path(absolute_css_url)
                    css_text = local_css_path.read_text(encoding='utf-8') if local_css_path.exists() else ""
                if css_text:
                    css_contents_to_analyze[absolute_css_url] = {'content': css_text, 'selector': f"external-css-{css_href}"}
            except Exception as e:
                logger.debug(f"Failed to retrieve/read external CSS from {absolute_css_url}: {e}")

        css_analyzer = CSSAnalyzer(level)
        _validate_css_cfg = self.config.get('validate_css', True)
        for css_source_id, css_data in css_contents_to_analyze.items():
            # Skip full-stylesheet validation on inline style="" fragments (declaration-only → false errors).
            css_analyzer.validate_css = _validate_css_cfg and '#inline-style-' not in css_source_id
            css_analyzer.file_path = css_source_id if not is_url else None
            css_analyzer.url = css_source_id if is_url else None
            css_issues, css_passed = css_analyzer.analyze(css_data['content'], css_source_id)
            for issue in css_issues:
                issue.additional_info.setdefault('source_reference', css_data.get('selector', ''))
                if '#inline-style-' in css_source_id or '#style-block-' in css_source_id:
                    issue.fix_type = FixType.MANUAL
                    issue.additional_info['remediation_note'] = (
                        "Inline and embedded CSS findings receive guidance only; edit them manually to avoid rewriting HTML."
                    )
            self.report.issues.extend(css_issues)
            self.report.passed_checks.extend(css_passed)

        # Dynamic + Axe analysis (live pages only)
        if page is not None:
            dynamic_analyzer = DynamicAnalyzer(level)
            dynamic_analyzer.file_path = current_file_path
            dynamic_analyzer.url = current_url
            d_issues, d_passed = await dynamic_analyzer.analyze(page, html_content, self.screenshot_output_dir)
            self.report.issues.extend(d_issues)
            self.report.passed_checks.extend(d_passed)

            axe_analyzer = AxeAnalyzer(level)
            axe_analyzer.file_path = current_file_path
            axe_analyzer.url = current_url
            a_issues, a_passed = await axe_analyzer.analyze(page, html_content, self.screenshot_output_dir)
            self.report.issues.extend(a_issues)
            self.report.passed_checks.extend(a_passed)

        # Feed the bounded cross-page consistency sample (3.2.3/3.2.4/3.2.6)
        if collect_fingerprint and self._consistency_budget > 0:
            try:
                self._consistency.add(soup, url_or_path)
                self._consistency_budget -= 1
            except Exception as e:
                logger.debug(f"Consistency accumulation failed for {url_or_path}: {e}")

    async def _run_for_url(self, url: str):
        """Run analysis for a URL target, honoring the configured scan scope."""
        urls_to_scan = {url}

        scope = self.config.get('scope', 'page')
        if scope in ('site', 'folder'):
            depth_cap = self.config.get('depth')
            if scope == 'folder':
                console.print("[cyan]Scope:[/cyan] current folder, restricted to the target's directory.")
            else:
                limit_desc = "no depth limit" if not depth_cap else f"max depth {depth_cap}"
                console.print(f"[cyan]Scope:[/cyan] entire site, {limit_desc}.")

            crawler = Crawler(
                start_url=url, max_depth=depth_cap,
                concurrency=self.config['concurrency'],
                exclude_patterns=list(self.config['exclude_urls']),
                user_agent=self.config['user_agent'], report=self.report,
                crawl_delay=self.config['crawl_delay'],
                max_urls_to_crawl=self.config['max_urls'],
                scope=scope, allow_private_hosts=self.config.get('allow_private_hosts', False)
            )
            crawled_urls = await crawler.crawl()
            urls_to_scan.update(crawled_urls)

            # Successfully fetched HTML pages are the authoritative analysis set.
            clean_urls_to_scan = set(self.report.all_urls_crawled)

            if self.config['max_urls'] is not None:
                clean_urls_to_scan = set(list(clean_urls_to_scan)[:self.config['max_urls']])

            urls_to_scan = sorted(list(clean_urls_to_scan))

        else:  # page scope analyzes only the single target page
            console.print("[cyan]Scope:[/cyan] current page only.")
            self.report.all_urls_crawled.add(url)
            urls_to_scan = [url]

        if not urls_to_scan:
            console.print("[bold yellow]No URLs to analyze after crawl/initial target check.[/bold yellow]")
            return

        console.print(f"Starting analysis of {len(urls_to_scan)} URL(s)...")
        await self._analyze_url_set(urls_to_scan)

        # Cross-page consistency checks (3.2.3 / 3.2.4 / 3.2.6) over the sampled pages
        if self._consistency.page_count >= 2:
            consistency_analyzer = ConsistencyAnalyzer(self.report.wcag_level_tested)
            ci, cp = consistency_analyzer.analyze(self._consistency)
            self.report.issues.extend(ci)
            self.report.passed_checks.extend(cp)
            console.print(f"[cyan]Cross-page consistency[/cyan] evaluated over {self._consistency.page_count} sampled page(s).")

    # Hard ceiling on full-fidelity (browser) pages, so pathological URL spaces can't explode the scan.
    MAX_FULL_PAGES = 2000

    @staticmethod
    def _template_key(url: str) -> str:
        """Normalize a URL into a coarse template key so structurally identical pages group together.

        Content identifiers collapse to placeholders (numeric -> '#', id-ish -> '*', slugs -> 's')
        while short structural section names are kept, so e.g. /installations/fort-bragg and
        /installations/camp-pendleton map to the same template.
        """
        p = urlparse(url)
        norm = []
        for seg in p.path.strip('/').split('/'):
            if not seg:
                continue
            low = seg.lower()
            if re.fullmatch(r'\d+', seg):
                norm.append('#')                              # numeric id / page number
            elif re.fullmatch(r'[0-9a-f]{8,}', low):
                norm.append('*')                              # hex / uuid
            elif re.search(r'\d', seg):
                norm.append('*')                              # contains a digit -> id-ish
            elif ('-' in seg or '_' in seg) and len(seg) > 3:
                norm.append('s')                              # hyphen/underscore slug -> content slug
            elif len(seg) > 20:
                norm.append('s')                              # long leaf -> content slug
            else:
                norm.append(low)                              # short structural keyword (section name)
        return p.netloc + '/' + '/'.join(norm) + ('?' if p.query else '')

    def _select_template_samples(self, urls: List[str], per_template: int) -> Set[str]:
        """Pick up to `per_template` representative URLs per template group for full-fidelity analysis."""
        groups: DefaultDict[str, List[str]] = defaultdict(list)
        for u in urls:
            groups[self._template_key(u)].append(u)
        chosen: List[str] = []
        for members in sorted(groups.values(), key=len, reverse=True):
            chosen.extend(sorted(members)[:per_template])

        capped = False
        if len(chosen) > self.MAX_FULL_PAGES:
            capped = True
            chosen = chosen[:self.MAX_FULL_PAGES]
        logger.info(f"Tiered sampling: {len(groups)} templates -> {len(chosen)} full-fidelity pages"
                    f"{' (capped at MAX_FULL_PAGES; remaining pages still get the static pass)' if capped else ''}.")
        if capped:
            console.print(f"[yellow]Note:[/yellow] full-fidelity pages capped at {self.MAX_FULL_PAGES} "
                          f"({len(groups)} templates detected); all other pages still receive the fast static pass.")
        return set(chosen)

    async def _wait_for_page(self, page: Page):
        """Readiness wait honoring the DOM-application mode (mirrors SortSite's None/Smart/All).

        'all' (default & best-results setting) applies every DOM change including analytics and
        lazy/intersection-triggered content by waiting for network idle and scrolling the page.
        Because the scan is tiered, this only runs on the per-template sampled pages, so the
        thoroughness does not hurt large-site throughput.
        """
        sel = self.config.get('wait_selector')
        dom_mode = (self.config.get('dom_mode') or 'all').lower()

        # An explicit selector wait always takes precedence.
        if sel:
            try:
                await page.wait_for_selector(sel, state='visible', timeout=30000)
            except PlaywrightError:
                logger.debug(f"Wait selector '{sel}' not found on {page.url} within 30s; proceeding.")
            return

        if dom_mode == 'none' and not self.config.get('wait_network'):
            return

        try:
            await page.wait_for_load_state('load', timeout=15000)
        except PlaywrightError:
            pass

        if dom_mode in ('smart', 'all') or self.config.get('wait_network'):
            try:
                await page.wait_for_load_state('networkidle', timeout=15000)
            except PlaywrightError:
                pass

        if dom_mode == 'all':
            # Apply ALL DOM changes: trigger lazy/intersection content by scrolling through the page.
            try:
                await page.evaluate("""
                    async () => {
                        await new Promise(resolve => {
                            let y = 0;
                            const step = Math.max(300, window.innerHeight);
                            const timer = setInterval(() => {
                                window.scrollTo(0, y); y += step;
                                if (y >= document.body.scrollHeight) { clearInterval(timer); resolve(); }
                            }, 50);
                            setTimeout(() => { clearInterval(timer); resolve(); }, 4000);
                        });
                        window.scrollTo(0, 0);
                    }
                """)
            except PlaywrightError:
                pass
            try:
                await page.wait_for_load_state('networkidle', timeout=8000)
            except PlaywrightError:
                pass

        await page.wait_for_timeout(150)

    async def _analyze_url_set(self, urls_to_scan: List[str]):
        """Analyze a set of URLs with bounded concurrency and tiered (template-sampled) fidelity."""
        bulk_mode = (self.config.get('bulk_mode') or 'tiered').lower()
        per_template = max(1, int(self.config.get('sample_per_template') or 3))
        analysis_conc = max(1, int(self.config.get('analysis_concurrency') or 8))
        static_conc = max(analysis_conc, analysis_conc * 2)
        delay = float(self.config.get('crawl_delay') or 0)

        if bulk_mode == 'full':
            full_urls = set(urls_to_scan) if self.browser else set()
        elif bulk_mode == 'static':
            full_urls = set()
        else:  # tiered
            full_urls = self._select_template_samples(urls_to_scan, per_template) if self.browser else set()

        full_list = [u for u in urls_to_scan if u in full_urls]
        bulk_list = [u for u in urls_to_scan if u not in full_urls]

        # Pages fed to the cross-page consistency sample (prioritize full-fidelity pages).
        consistency_urls: Set[str] = set(full_list[:self._consistency_budget])
        for u in bulk_list:
            if len(consistency_urls) >= self._consistency_budget:
                break
            consistency_urls.add(u)

        console.print(f"[cyan]Tiered scan[/cyan] (mode={bulk_mode}): {len(full_list)} full browser+axe+dynamic, "
                      f"{len(bulk_list)} fast static; up to {analysis_conc} pages in parallel.")

        timeout = aiohttp.ClientTimeout(total=30)
        headers = {'User-Agent': self.config['user_agent']}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers,
                                         connector=_safe_connector(self.config.get('allow_private_hosts', False))) as session:
            context = await self._new_secure_browser_context() if (self.browser and full_list) else None

            full_q: asyncio.Queue = asyncio.Queue()
            static_q: asyncio.Queue = asyncio.Queue()
            for u in full_list:
                full_q.put_nowait(u)
            for u in bulk_list:
                static_q.put_nowait(u)

            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                          BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), console=console) as progress:
                scan_task = progress.add_task("[cyan]Analyzing pages...", total=len(urls_to_scan))

                async def full_worker():
                    while True:
                        try:
                            u = full_q.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        page = None
                        try:
                            if delay:
                                await asyncio.sleep(delay)
                            page = await context.new_page()
                            await page.goto(u, wait_until='domcontentloaded', timeout=60000)
                            expected_host = (urlparse(u).hostname or '').lower().rstrip('.')
                            if not _same_host_family(page.url, expected_host, True):
                                raise ValueError("Top-level browser navigation redirected outside scan scope")
                            await self._wait_for_page(page)
                            html = await page.content()
                            if len(html.encode('utf-8', errors='ignore')) > MAX_BROWSER_DOM_BYTES:
                                raise ValueError(f"Rendered DOM exceeds {MAX_BROWSER_DOM_BYTES} bytes")
                            self.report.all_urls_crawled.add(u)
                            await self._analyze_page_content(html, u, page=page, session=session,
                                                             collect_fingerprint=(u in consistency_urls))
                        except PlaywrightError as e:
                            logger.error(f"Playwright error analyzing {u}: {e}")
                        except Exception as e:
                            logger.error(f"Error analyzing {u}: {e}")
                        finally:
                            if page:
                                try:
                                    await page.close()
                                except Exception as close_error:
                                    logger.debug("Could not close browser page cleanly: %s", close_error)
                            progress.update(scan_task, advance=1, description=f"Analyzing [magenta]{u[:70]}[/magenta]")

                async def static_worker():
                    while True:
                        try:
                            u = static_q.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        try:
                            if delay:
                                await asyncio.sleep(delay)
                            html = ""
                            async with _safe_get(
                                session, u,
                                allow_private=self.config.get('allow_private_hosts', False),
                                timeout=timeout,
                            ) as resp:
                                expected_host = (urlparse(u).hostname or '').lower().rstrip('.')
                                if not _same_host_family(str(resp.url), expected_host, True):
                                    raise ValueError("Static request redirected outside scan scope")
                                self.report.all_urls_crawled.add(u)
                                if resp.status < 400 and 'html' in resp.headers.get('Content-Type', '').lower():
                                    html = await _read_capped_text(resp)
                            if html:
                                await self._analyze_page_content(html, u, page=None, session=session,
                                                                 collect_fingerprint=(u in consistency_urls))
                        except Exception as e:
                            logger.debug(f"Static analysis failed for {u}: {e}")
                        finally:
                            progress.update(scan_task, advance=1, description=f"Analyzing [magenta]{u[:70]}[/magenta]")

                workers = []
                if context:
                    workers += [asyncio.create_task(full_worker()) for _ in range(analysis_conc)]
                workers += [asyncio.create_task(static_worker()) for _ in range(static_conc)]
                await asyncio.gather(*workers, return_exceptions=True)

            if context:
                await context.close()

    async def _run_for_local_dir(self, dir_path: str):
        """Run analysis for local directory."""
        html_files = sorted(list(Path(dir_path).rglob('*.html')) + list(Path(dir_path).rglob('*.htm')))
        console.print(f"Found {len(html_files)} HTML files to analyze in '{dir_path}'...")

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                     BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), console=console) as progress:
            scan_task = progress.add_task("[cyan]Analyzing local files...", total=len(html_files))
            for file_path in html_files:
                progress.update(scan_task, description=f"Analyzing [magenta]{file_path.name}[/magenta]")
                file_uri = file_path.as_uri()
                try:
                    content = file_path.read_text(encoding='utf-8')
                    self.report.all_files_analyzed.add(str(file_path.resolve()))
                    await self._analyze_page_content(content, file_uri, page=None)
                except Exception as e:
                    logger.error(f"Could not analyze file {file_path}: {e}")
                progress.advance(scan_task)

    async def _run_for_local_file(self, file_path: str):
        """Run analysis for single local file."""
        console.print(f"Analyzing single file: {file_path}")
        file_uri = Path(file_path).as_uri()
        try:
            content = Path(file_path).read_text(encoding='utf-8')
            self.report.all_files_analyzed.add(str(Path(file_path).resolve()))
            await self._analyze_page_content(content, file_uri, page=None)
        except Exception as e:
            logger.error(f"Could not analyze file {file_path}: {e}")


# ==============================================================================
# CONSERVATIVE PDF EVIDENCE ENGINE
# ==============================================================================

class EvidenceStatus(str, Enum):
    """Outcome of one narrow automated accessibility test."""

    FAIL = "Fail"
    # This is a result label, not a credential.
    PASS = "Pass (narrow automated test)"  # noqa: S105  # nosec B105
    NEEDS_REVIEW = "Needs manual review"
    NOT_APPLICABLE = "Not applicable"
    NOT_TESTED = "Not tested"
    ERROR = "Analysis error"


@dataclass
class PDFEvidence:
    rule_id: str
    criteria: List[str]
    title: str
    status: EvidenceStatus
    severity: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class PDFDocumentReport:
    source: str
    local_path: str
    sha256: str = ""
    file_size_bytes: int = 0
    page_count: int = 0
    encrypted: bool = False
    pdf_version: str = ""
    title: str = ""
    language: str = ""
    tagged: bool = False
    pdfua_identifier: str = ""
    duration_seconds: float = 0.0
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evidence: List[PDFEvidence] = field(default_factory=list)
    error: str = ""

    def add(
        self,
        rule_id: str,
        criteria: Iterable[str],
        title: str,
        status: EvidenceStatus,
        severity: str,
        message: str,
        evidence: Optional[Dict[str, Any]] = None,
        remediation: str = "",
    ) -> None:
        self.evidence.append(PDFEvidence(
            rule_id=rule_id,
            criteria=list(criteria),
            title=title,
            status=status,
            severity=severity,
            message=message,
            evidence=evidence or {},
            remediation=remediation,
        ))

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["evidence"] = [item.to_dict() for item in self.evidence]
        counts: DefaultDict[str, int] = defaultdict(int)
        for item in self.evidence:
            counts[item.status.value] += 1
        value["summary"] = dict(sorted(counts.items()))
        return value


def _pdf_string(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _pdf_name(value: Any) -> str:
    return _pdf_string(value).lstrip('/')


def _pdf_get(mapping: Any, key: str, default: Any = None) -> Any:
    try:
        return mapping.get(key, default)
    except Exception:
        return default


def _valid_language_tag(value: str) -> bool:
    """Conservative syntax check for common BCP 47 language tags."""
    if not value or len(value) > 63 or '_' in value:
        return False
    return bool(re.fullmatch(
        r"(?i)(?:[a-z]{2,3}|[a-z]{4}|[a-z]{5,8})"
        r"(?:-[a-z]{4})?(?:-(?:[a-z]{2}|\d{3}))?"
        r"(?:-(?:[a-z0-9]{5,8}|\d[a-z0-9]{3}))*"
        r"(?:-[0-9a-wy-z](?:-[a-z0-9]{2,8})+)*"
        r"(?:-x(?:-[a-z0-9]{1,8})+)?",
        value,
    ))


def _object_identity(obj: Any) -> Any:
    try:
        objgen = obj.objgen
        if objgen != (0, 0):
            return ("obj", objgen)
    except Exception:
        return ("mem", id(obj))
    return ("mem", id(obj))


def _inspect_pdf_in_process(
    path: Path,
    max_file_bytes: int,
    max_pages: int = DEFAULT_MAX_PDF_PAGES,
) -> Dict[str, Any]:
    """Inspect one PDF conservatively. Called in a disposable child process."""
    started = time.monotonic()
    resolved = path.resolve()
    report = PDFDocumentReport(source=str(resolved), local_path=str(resolved))

    try:
        stat = resolved.stat()
        report.file_size_bytes = stat.st_size
        if stat.st_size <= 0:
            raise ValueError("PDF is empty")
        if stat.st_size > max_file_bytes:
            raise ValueError(f"PDF exceeds the {max_file_bytes}-byte safety limit")
        with resolved.open('rb') as handle:
            header = handle.read(8)
            digest = hashlib.sha256()
            digest.update(header)
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        if not header.startswith(b'%PDF-'):
            raise ValueError("File does not begin with a PDF signature")
        report.sha256 = digest.hexdigest()
    except Exception as exc:
        report.error = str(exc)
        report.add(
            "PDF.FILE.READABLE", [], "Readable PDF file", EvidenceStatus.ERROR,
            "Critical", str(exc), remediation="Provide a complete, readable PDF within the configured size limit.",
        )
        report.duration_seconds = round(time.monotonic() - started, 3)
        return report.to_dict()

    try:
        import pikepdf
    except ImportError:
        report.error = "pikepdf is not installed"
        report.add(
            "PDF.ENGINE.AVAILABLE", [], "PDF analysis engine", EvidenceStatus.ERROR,
            "Critical", report.error, remediation="Install dependencies from requirements.txt.",
        )
        report.duration_seconds = round(time.monotonic() - started, 3)
        return report.to_dict()

    try:
        with pikepdf.open(resolved) as pdf:
            report.page_count = len(pdf.pages)
            if report.page_count > max_pages:
                report.error = f"PDF has {report.page_count} pages, exceeding the {max_pages}-page safety limit"
                report.add(
                    "PDF.PAGE.LIMIT", [], "PDF page safety limit", EvidenceStatus.ERROR,
                    "Critical", report.error,
                    {"pages": report.page_count, "limit": max_pages},
                    "Increase the limit only in a resource-constrained disposable environment.",
                )
                report.duration_seconds = round(time.monotonic() - started, 3)
                return report.to_dict()
            report.encrypted = bool(pdf.is_encrypted)
            report.pdf_version = _pdf_string(getattr(pdf, 'pdf_version', ''))
            root = pdf.Root
            mark_info = _pdf_get(root, '/MarkInfo')
            marked = bool(_pdf_get(mark_info, '/Marked', False)) if mark_info is not None else False
            struct_root = _pdf_get(root, '/StructTreeRoot')
            report.tagged = bool(marked and struct_root is not None)
            report.language = _pdf_string(_pdf_get(root, '/Lang')).lstrip('/')

            docinfo = getattr(pdf, 'docinfo', {})
            report.title = _pdf_string(_pdf_get(docinfo, '/Title'))
            viewer_prefs = _pdf_get(root, '/ViewerPreferences')
            display_title = bool(_pdf_get(viewer_prefs, '/DisplayDocTitle', False)) if viewer_prefs is not None else False

            xmp_values: Dict[str, str] = {}
            try:
                with pdf.open_metadata(set_pikepdf_as_editor=False) as metadata:
                    for key in ('dc:title', 'pdfuaid:part', 'pdfuaid:rev', 'xmp:CreatorTool'):
                        try:
                            value = metadata.get(key)
                        except Exception:
                            value = None
                        if value:
                            xmp_values[key] = _pdf_string(value)
            except Exception as metadata_error:
                logger.debug("Could not read PDF XMP metadata: %s", metadata_error)
            if not report.title:
                report.title = xmp_values.get('dc:title', '')
            report.pdfua_identifier = xmp_values.get('pdfuaid:part', '')

            roles: DefaultDict[str, int] = defaultdict(int)
            figure_missing_alt = 0
            figure_with_alt = 0
            heading_levels: List[int] = []
            generic_headings = 0
            structure_nodes = 0
            structure_truncated = False
            seen_structure: Set[Any] = set()

            role_map = _pdf_get(struct_root, '/RoleMap', {}) if struct_root is not None else {}

            def mapped_role(value: Any) -> str:
                raw = '/' + _pdf_name(value)
                mapped = _pdf_get(role_map, raw, raw)
                return _pdf_name(mapped)

            def walk_structure(obj: Any, depth: int = 0) -> None:
                nonlocal figure_missing_alt, figure_with_alt, generic_headings
                nonlocal structure_nodes, structure_truncated
                if obj is None or depth > 128 or structure_nodes >= 100000:
                    if depth > 128 or structure_nodes >= 100000:
                        structure_truncated = True
                    return
                identity = _object_identity(obj)
                if identity in seen_structure:
                    return
                seen_structure.add(identity)

                if isinstance(obj, pikepdf.Array):
                    for child in obj:
                        walk_structure(child, depth + 1)
                    return
                if not isinstance(obj, pikepdf.Dictionary):
                    return

                structure_nodes += 1
                role = mapped_role(_pdf_get(obj, '/S'))
                if role:
                    roles[role] += 1
                    if role == 'Figure':
                        alt = _pdf_string(_pdf_get(obj, '/Alt') or _pdf_get(obj, '/ActualText'))
                        if alt:
                            figure_with_alt += 1
                        else:
                            figure_missing_alt += 1
                    elif re.fullmatch(r'H[1-6]', role):
                        heading_levels.append(int(role[1]))
                    elif role == 'H':
                        generic_headings += 1
                walk_structure(_pdf_get(obj, '/K'), depth + 1)

            if struct_root is not None:
                walk_structure(_pdf_get(struct_root, '/K'))

            image_objects = 0
            link_annotations = 0
            link_annotations_without_contents = 0
            widget_annotations = 0
            rich_media_annotations = 0
            javascript_actions = 0
            page_tab_order_struct = 0
            seen_xobjects: Set[Any] = set()

            def inspect_resources(resources: Any, depth: int = 0) -> None:
                nonlocal image_objects
                if resources is None or depth > 16:
                    return
                xobjects = _pdf_get(resources, '/XObject', {})
                try:
                    values = list(xobjects.values())
                except Exception:
                    values = []
                for xobject in values:
                    identity = _object_identity(xobject)
                    if identity in seen_xobjects:
                        continue
                    seen_xobjects.add(identity)
                    subtype = _pdf_name(_pdf_get(xobject, '/Subtype'))
                    if subtype == 'Image':
                        image_objects += 1
                    elif subtype == 'Form':
                        inspect_resources(_pdf_get(xobject, '/Resources'), depth + 1)

            def action_is_javascript(action: Any) -> bool:
                return _pdf_name(_pdf_get(action, '/S')) == 'JavaScript'

            for page in pdf.pages:
                page_obj = page.obj
                inspect_resources(_pdf_get(page_obj, '/Resources'))
                if _pdf_name(_pdf_get(page_obj, '/Tabs')) == 'S':
                    page_tab_order_struct += 1
                annotations = _pdf_get(page_obj, '/Annots', [])
                try:
                    annotation_values = list(annotations)
                except Exception:
                    annotation_values = []
                for annotation in annotation_values:
                    subtype = _pdf_name(_pdf_get(annotation, '/Subtype'))
                    if subtype == 'Link':
                        link_annotations += 1
                        if not _pdf_string(_pdf_get(annotation, '/Contents')):
                            link_annotations_without_contents += 1
                    elif subtype == 'Widget':
                        widget_annotations += 1
                    elif subtype in {'RichMedia', 'Movie', 'Sound', 'Screen', '3D'}:
                        rich_media_annotations += 1
                    if action_is_javascript(_pdf_get(annotation, '/A')) or action_is_javascript(_pdf_get(annotation, '/AA')):
                        javascript_actions += 1

            if action_is_javascript(_pdf_get(root, '/OpenAction')):
                javascript_actions += 1
            catalog_actions = _pdf_get(root, '/AA')
            if catalog_actions is not None:
                try:
                    javascript_actions += sum(
                        1 for action in catalog_actions.values() if action_is_javascript(action)
                    )
                except Exception as action_error:
                    logger.debug("Could not inspect catalog actions: %s", action_error)

            names = _pdf_get(root, '/Names')
            javascript_name_tree = _pdf_get(names, '/JavaScript') if names is not None else None
            embedded_name_tree = _pdf_get(names, '/EmbeddedFiles') if names is not None else None
            if javascript_name_tree is not None:
                javascript_actions += 1
            has_embedded_files = embedded_name_tree is not None

            acroform = _pdf_get(root, '/AcroForm')
            fields = _pdf_get(acroform, '/Fields', []) if acroform is not None else []
            form_fields = 0
            form_fields_without_tooltip = 0
            seen_fields: Set[Any] = set()

            def walk_fields(items: Any, depth: int = 0) -> None:
                nonlocal form_fields, form_fields_without_tooltip, javascript_actions
                if items is None or depth > 64:
                    return
                try:
                    values = list(items)
                except Exception:
                    values = []
                for field_obj in values:
                    identity = _object_identity(field_obj)
                    if identity in seen_fields:
                        continue
                    seen_fields.add(identity)
                    kids = _pdf_get(field_obj, '/Kids')
                    field_type = _pdf_name(_pdf_get(field_obj, '/FT'))
                    if field_type or kids is None:
                        form_fields += 1
                        if not _pdf_string(_pdf_get(field_obj, '/TU')):
                            form_fields_without_tooltip += 1
                    if action_is_javascript(_pdf_get(field_obj, '/A')) or action_is_javascript(_pdf_get(field_obj, '/AA')):
                        javascript_actions += 1
                    if kids is not None:
                        walk_fields(kids, depth + 1)

            walk_fields(fields)

            outlines = _pdf_get(root, '/Outlines')
            has_bookmarks = bool(outlines is not None and _pdf_get(outlines, '/First') is not None)

            report.add(
                "PDF.FILE.PARSED", [], "PDF parsed successfully", EvidenceStatus.PASS,
                "Informational", f"Parsed {report.page_count} page(s).",
                {"pages": report.page_count, "encrypted": report.encrypted, "pdf_version": report.pdf_version},
            )

            report.add(
                "PDF.TAGGED.STRUCTURE", ["1.3.1", "1.3.2"], "Tagged document structure",
                EvidenceStatus.PASS if report.tagged else EvidenceStatus.FAIL,
                "Critical" if not report.tagged else "Informational",
                "The catalog declares a structure tree and MarkInfo/Marked is true."
                if report.tagged else "A complete tagged structure declaration was not found.",
                {"mark_info_marked": marked, "structure_tree_present": struct_root is not None, "structure_nodes": structure_nodes},
                "Tag the PDF and verify every meaningful object is represented correctly in the structure tree.",
            )

            if report.language and _valid_language_tag(report.language):
                lang_status, lang_severity = EvidenceStatus.PASS, "Informational"
                lang_message = f"The document language is set to '{report.language}' and has valid tag syntax."
            elif report.language:
                lang_status, lang_severity = EvidenceStatus.FAIL, "Serious"
                lang_message = f"The document language value '{report.language}' is not valid BCP 47 syntax."
            else:
                lang_status, lang_severity = EvidenceStatus.FAIL, "Serious"
                lang_message = "The document catalog has no language value."
            report.add(
                "PDF.LANGUAGE.DOCUMENT", ["3.1.1"], "Document language", lang_status,
                lang_severity, lang_message, {"language": report.language},
                "Set the document language and verify language changes within content are tagged.",
            )

            report.add(
                "PDF.TITLE.METADATA", ["2.4.2"], "Document title metadata",
                EvidenceStatus.PASS if report.title else EvidenceStatus.FAIL,
                "Serious" if not report.title else "Informational",
                f"Document title metadata is '{report.title}'." if report.title else "Document title metadata is empty or absent.",
                {"title": report.title}, "Add a concise, descriptive document title.",
            )
            report.add(
                "PDF.TITLE.DISPLAY", ["2.4.2"], "Viewer displays document title",
                EvidenceStatus.PASS if display_title else EvidenceStatus.NEEDS_REVIEW,
                "Moderate" if not display_title else "Informational",
                "ViewerPreferences/DisplayDocTitle is enabled."
                if display_title else "DisplayDocTitle is not enabled; verify the intended viewer presents the descriptive title.",
                {"display_doc_title": display_title},
                "Configure the initial view to display the document title where supported.",
            )

            total_struct_figures = figure_with_alt + figure_missing_alt
            if not report.tagged:
                figure_status = EvidenceStatus.NOT_TESTED
                figure_message = "Figure alternatives cannot be reliably evaluated because the PDF is not tagged."
                figure_severity = "Serious"
            elif total_struct_figures == 0 and image_objects == 0:
                figure_status = EvidenceStatus.NOT_APPLICABLE
                figure_message = "No figure structure elements or image XObjects were detected."
                figure_severity = "Informational"
            elif figure_missing_alt:
                figure_status = EvidenceStatus.FAIL
                figure_message = f"{figure_missing_alt} of {total_struct_figures} Figure element(s) lack Alt or ActualText."
                figure_severity = "Critical"
            elif total_struct_figures:
                figure_status = EvidenceStatus.PASS
                figure_message = f"All {total_struct_figures} Figure element(s) have Alt or ActualText."
                figure_severity = "Informational"
            else:
                figure_status = EvidenceStatus.NEEDS_REVIEW
                figure_message = f"Detected {image_objects} image object(s), but no Figure structure elements were found. Verify artifacts and meaningful images."
                figure_severity = "Serious"
            report.add(
                "PDF.FIGURE.ALTERNATIVES", ["1.1.1"], "Figure text alternatives",
                figure_status, figure_severity, figure_message,
                {"image_objects": image_objects, "figures": total_struct_figures, "figures_missing_alt": figure_missing_alt},
                "Give meaningful figures accurate alternative text and mark decorative content as artifacts.",
            )

            heading_jumps = [
                [previous, current]
                for previous, current in zip(heading_levels, heading_levels[1:], strict=False)
                if current > previous + 1
            ]
            if not report.tagged:
                heading_status = EvidenceStatus.NOT_TESTED
                heading_message = "Heading semantics cannot be reliably evaluated because the PDF is not tagged."
            elif not heading_levels and not generic_headings:
                heading_status = EvidenceStatus.NEEDS_REVIEW
                heading_message = "No heading structure elements were detected; verify whether the document requires headings."
            else:
                heading_status = EvidenceStatus.NEEDS_REVIEW
                heading_message = "Heading tags were detected. Their wording, hierarchy, and visual correspondence require review."
            report.add(
                "PDF.HEADINGS.STRUCTURE", ["1.3.1", "2.4.6"], "Heading structure",
                heading_status, "Moderate", heading_message,
                {"heading_levels": heading_levels[:500], "generic_headings": generic_headings, "possible_level_jumps": heading_jumps[:100]},
                "Verify headings are descriptive, correctly nested, and match the visual organization.",
            )

            report.add(
                "PDF.READING_ORDER", ["1.3.2", "2.4.3"], "Reading and focus order",
                EvidenceStatus.NEEDS_REVIEW if report.tagged else EvidenceStatus.NOT_TESTED,
                "Serious",
                "A structure tree is present, but meaningful reading and focus order require manual verification."
                if report.tagged else "Reading and focus order cannot be established without a tagged structure tree.",
                {"tagged": report.tagged},
                "Review the tag tree and keyboard focus sequence against the intended visual and logical order.",
            )

            table_count = roles.get('Table', 0)
            if table_count:
                table_status, table_message = EvidenceStatus.NEEDS_REVIEW, f"Detected {table_count} tagged table(s); header associations require manual verification."
            else:
                table_status, table_message = EvidenceStatus.NOT_APPLICABLE, "No tagged tables were detected."
            report.add(
                "PDF.TABLE.SEMANTICS", ["1.3.1"], "Table semantics", table_status,
                "Serious" if table_count else "Informational", table_message,
                {"tables": table_count, "rows": roles.get('TR', 0), "headers": roles.get('TH', 0), "cells": roles.get('TD', 0)},
                "Verify table headers, scope, spans, and reading order with a screen reader.",
            )

            if link_annotations:
                link_status = EvidenceStatus.NEEDS_REVIEW
                link_message = f"Detected {link_annotations} link annotation(s); purpose and keyboard behavior require review."
                link_severity = "Moderate"
            else:
                link_status = EvidenceStatus.NOT_APPLICABLE
                link_message = "No link annotations were detected."
                link_severity = "Informational"
            report.add(
                "PDF.LINK.PURPOSE", ["2.1.1", "2.4.4"], "Link purpose and operation",
                link_status, link_severity, link_message,
                {"links": link_annotations, "annotations_without_contents": link_annotations_without_contents, "tagged_link_elements": roles.get('Link', 0)},
                "Verify every link has meaningful context, a matching tag, and complete keyboard operation.",
            )

            if form_fields:
                if form_fields_without_tooltip:
                    form_status = EvidenceStatus.FAIL
                    form_message = f"{form_fields_without_tooltip} of {form_fields} form field(s) lack a TU tooltip."
                    form_severity = "Critical"
                else:
                    form_status = EvidenceStatus.NEEDS_REVIEW
                    form_message = f"All {form_fields} field(s) have tooltips; labels, instructions, errors, and operation still require review."
                    form_severity = "Serious"
            else:
                form_status = EvidenceStatus.NOT_APPLICABLE
                form_message = "No AcroForm fields were detected."
                form_severity = "Informational"
            report.add(
                "PDF.FORM.LABELS", ["1.3.1", "3.3.2", "4.1.2"], "Form field labels and operation",
                form_status, form_severity, form_message,
                {"fields": form_fields, "missing_tooltips": form_fields_without_tooltip, "widget_annotations": widget_annotations, "pages_with_struct_tab_order": page_tab_order_struct},
                "Provide accurate tooltips and verify labels, instructions, error handling, focus order, and keyboard access.",
            )

            if report.page_count >= 20 and not has_bookmarks:
                nav_status = EvidenceStatus.NEEDS_REVIEW
                nav_message = "This longer document has no detected bookmarks; verify that multiple navigation methods are available."
            elif has_bookmarks:
                nav_status = EvidenceStatus.NEEDS_REVIEW
                nav_message = "Bookmarks are present; verify their labels, hierarchy, destinations, and completeness."
            else:
                nav_status = EvidenceStatus.NOT_APPLICABLE
                nav_message = "A bookmark review was not triggered for this short document."
            report.add(
                "PDF.NAVIGATION.BOOKMARKS", ["2.4.5"], "Document navigation",
                nav_status, "Moderate" if nav_status == EvidenceStatus.NEEDS_REVIEW else "Informational",
                nav_message, {"pages": report.page_count, "bookmarks_present": has_bookmarks},
                "Provide accurate bookmarks or another additional navigation method for longer documents.",
            )

            dynamic_features = rich_media_annotations + javascript_actions
            report.add(
                "PDF.DYNAMIC.CONTENT", ["1.2.1", "1.2.2", "2.2.1", "2.2.2", "2.3.1"],
                "Time-based and dynamic content",
                EvidenceStatus.NEEDS_REVIEW if dynamic_features else EvidenceStatus.NOT_APPLICABLE,
                "Critical" if dynamic_features else "Informational",
                f"Detected {rich_media_annotations} rich-media annotation(s) and {javascript_actions} JavaScript indicator(s)."
                if dynamic_features else "No rich-media annotations or JavaScript indicators were detected.",
                {"rich_media_annotations": rich_media_annotations, "javascript_indicators": javascript_actions},
                "Manually test captions, audio description, timing, pause controls, flashing, and keyboard behavior for dynamic content.",
            )

            report.add(
                "PDF.SECURITY.ACTIVE_CONTENT", [], "Active content security review",
                EvidenceStatus.NEEDS_REVIEW if javascript_actions else EvidenceStatus.PASS,
                "Serious" if javascript_actions else "Informational",
                f"Detected {javascript_actions} JavaScript indicator(s). Treat this document as active content."
                if javascript_actions else "No catalog, annotation, or form JavaScript indicators were detected by this narrow check.",
                {"javascript_indicators": javascript_actions},
                "Remove unnecessary active content and review required scripts in a disposable environment.",
            )
            report.add(
                "PDF.SECURITY.EMBEDDED_FILES", [], "Embedded file security review",
                EvidenceStatus.NEEDS_REVIEW if has_embedded_files else EvidenceStatus.PASS,
                "Serious" if has_embedded_files else "Informational",
                "The document contains an embedded-files name tree."
                if has_embedded_files else "No embedded-files name tree was detected by this narrow check.",
                {"embedded_files_name_tree": has_embedded_files},
                "Remove unnecessary attachments and review required attachments separately before opening them.",
            )

            report.add(
                "PDF.PDFUA.IDENTIFIER", [], "PDF/UA metadata identifier",
                EvidenceStatus.PASS if report.pdfua_identifier else EvidenceStatus.NEEDS_REVIEW,
                "Informational",
                f"PDF/UA part identifier is '{report.pdfua_identifier}'."
                if report.pdfua_identifier else "No PDF/UA part identifier was detected; this does not by itself prove nonconformance.",
                {"pdfua_part": report.pdfua_identifier},
                "Use a dedicated PDF/UA validator and manual assistive-technology testing for a conformance claim.",
            )

            report.add(
                "PDF.MANUAL.CONTRAST", ["1.4.3", "1.4.11"], "Contrast and use of color",
                EvidenceStatus.NEEDS_REVIEW, "Serious",
                "Text, graphical-object, focus-indicator, and color-only meaning checks require rendered visual analysis and human review.",
                remediation="Measure contrast and verify that color is not the only means of conveying information.",
            )
            if structure_truncated:
                report.add(
                    "PDF.STRUCTURE.LIMIT", [], "Structure analysis limit", EvidenceStatus.ERROR,
                    "Serious", "The structure walk reached its depth or node safety limit.",
                    {"nodes_processed": structure_nodes},
                    "Review the document manually and investigate an unusually large or recursive structure tree.",
                )

    except Exception as exc:
        report.error = f"PDF parser error: {exc}"
        report.add(
            "PDF.FILE.PARSE", [], "PDF parser completed", EvidenceStatus.ERROR,
            "Critical", report.error,
            remediation="Open the file only in a disposable environment, repair or regenerate it, and scan again.",
        )

    report.evidence.sort(key=lambda item: (item.rule_id, item.status.value, item.message))
    report.duration_seconds = round(time.monotonic() - started, 3)
    return report.to_dict()


def _validate_http_url(url: str) -> str:
    """Validate URL syntax before the SSRF-aware connector resolves it."""
    value = str(url).strip()
    if not value or len(value) > MAX_URL_LENGTH or re.search(r'[\x00-\x20]', value):
        raise ValueError("URL is empty, too long, or contains control characters")
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'}:
        raise ValueError("Only HTTP and HTTPS URLs are accepted")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must contain a hostname and must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL contains an invalid port")
    return value


async def _download_pdf(
    session: aiohttp.ClientSession,
    url: str,
    destination_dir: Path,
    max_bytes: int,
    allow_private_hosts: bool,
    max_redirects: int = 5,
) -> Path:
    """Download one PDF with redirect, size, signature, and atomic-write controls."""
    current = _validate_http_url(url)
    await _assert_safe_destination(current, allow_private_hosts)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.pdf"
    if destination.exists():
        with destination.open('rb') as existing:
            existing_header = existing.read(5)
        if destination.stat().st_size <= max_bytes and existing_header == b'%PDF-':
            return destination
        destination.unlink(missing_ok=True)

    for hop in range(max_redirects + 1):
        await _assert_safe_destination(current, allow_private_hosts)
        async with session.get(current, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                if hop >= max_redirects:
                    raise ValueError("PDF download exceeded the redirect limit")
                location = response.headers.get('Location')
                if not location:
                    raise ValueError("PDF redirect has no Location header")
                current = _validate_http_url(urljoin(current, location))
                continue
            if response.status != 200:
                raise ValueError(f"PDF download returned HTTP {response.status}")
            if response.content_length and response.content_length > max_bytes:
                raise ValueError(f"PDF download exceeds the {max_bytes}-byte safety limit")

            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix='.pdf-download-', suffix='.part', dir=str(destination_dir)
            )
            temporary_path = Path(temporary_name)
            total = 0
            first_bytes = bytearray()
            try:
                with os.fdopen(file_descriptor, 'wb') as handle:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"PDF download exceeds the {max_bytes}-byte safety limit")
                        if len(first_bytes) < 8:
                            first_bytes.extend(chunk[: 8 - len(first_bytes)])
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if total == 0 or not bytes(first_bytes).startswith(b'%PDF-'):
                    raise ValueError("Downloaded content does not have a PDF signature")
                os.replace(temporary_path, destination)
                return destination
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
    raise ValueError("PDF download could not be completed")


def _pdf_error_result(source: str, message: str) -> Dict[str, Any]:
    report = PDFDocumentReport(source=source, local_path="", error=message)
    report.add(
        "PDF.INPUT.ERROR", [], "PDF input preparation", EvidenceStatus.ERROR,
        "Critical", message, remediation="Verify the path or URL and retry.",
    )
    return report.to_dict()


def _run_pdf_worker(
    path: Path,
    max_bytes: int,
    timeout_seconds: int,
    max_pages: int,
) -> Dict[str, Any]:
    """Analyze a PDF in a child process that can be terminated on timeout."""
    command = [
        sys.executable,
        '-B',
        str(Path(__file__).resolve()),
        '_pdf-worker',
        str(path.resolve()),
        '--max-file-bytes',
        str(max_bytes),
        '--max-pages',
        str(max_pages),
    ]
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        # The interpreter and this script path are fixed, and no shell is used.
        completed = subprocess.run(  # noqa: S603  # nosec B603
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_seconds,
            env=environment,
            creationflags=creation_flags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _pdf_error_result(str(path), f"PDF analysis exceeded {timeout_seconds} seconds and was terminated")
    except Exception as exc:
        return _pdf_error_result(str(path), f"Could not start isolated PDF analysis: {exc}")

    if len(completed.stdout.encode('utf-8', errors='ignore')) > 8 * 1024 * 1024:
        return _pdf_error_result(str(path), "PDF worker output exceeded its safety limit")
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2000:] or f"worker exited with code {completed.returncode}"
        return _pdf_error_result(str(path), f"PDF worker failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = completed.stderr.strip()[-1000:]
        return _pdf_error_result(str(path), f"PDF worker returned invalid output. {detail}".strip())


async def _prepare_pdf_jobs(
    targets: Iterable[str],
    url_file: Optional[str],
    download_dir: Path,
    max_bytes: int,
    workers: int,
    allow_private_hosts: bool,
    max_documents: int,
) -> Tuple[List[Tuple[str, Path]], List[Dict[str, Any]]]:
    raw_targets = [str(item).strip() for item in targets if str(item).strip()]
    errors: List[Dict[str, Any]] = []
    if url_file:
        list_path = Path(url_file).resolve()
        try:
            if list_path.stat().st_size > 5 * 1024 * 1024:
                raise ValueError("URL list exceeds 5 MiB")
            for line in list_path.read_text(encoding='utf-8-sig').splitlines()[:max_documents]:
                line = line.strip()
                if line and not line.startswith('#'):
                    raw_targets.append(line)
        except Exception as exc:
            errors.append(_pdf_error_result(str(list_path), f"Could not read URL list: {exc}"))
    if len(raw_targets) > max_documents:
        errors.append(_pdf_error_result(
            "input-set",
            f"Input contained more than the configured {max_documents} document limit; excess entries were not scanned",
        ))
        raw_targets = raw_targets[:max_documents]

    local_jobs: List[Tuple[str, Path]] = []
    remote_urls: List[str] = []
    seen_sources: Set[str] = set()
    for target in raw_targets:
        if target in seen_sources:
            continue
        seen_sources.add(target)
        if target.lower().startswith(('http://', 'https://')):
            try:
                remote_urls.append(_validate_http_url(target))
            except Exception as exc:
                errors.append(_pdf_error_result(target, str(exc)))
            continue

        path = Path(target).expanduser().resolve()
        if path.is_dir():
            remaining = max(0, max_documents - len(local_jobs) - len(remote_urls))
            matches: List[Path] = []
            if remaining:
                for item in path.rglob('*'):
                    if item.is_file() and item.suffix.lower() == '.pdf':
                        matches.append(item)
                        if len(matches) >= remaining:
                            break
            matches.sort(key=lambda item: str(item).lower())
            if not matches:
                errors.append(_pdf_error_result(str(path), "Directory contains no PDF files"))
            local_jobs.extend((str(item), item) for item in matches)
        elif path.is_file() and path.suffix.lower() == '.pdf':
            local_jobs.append((str(path), path))
        else:
            errors.append(_pdf_error_result(str(path), "Local target is not an existing PDF file or directory"))
        if len(local_jobs) + len(remote_urls) >= max_documents:
            break

    if remote_urls:
        timeout = aiohttp.ClientTimeout(total=180, connect=15, sock_read=60)
        connector = _safe_connector(allow_private_hosts, limit=max(1, min(workers, 16)))
        semaphore = asyncio.Semaphore(max(1, min(workers, 16)))
        headers = {'User-Agent': DEFAULT_USER_AGENT, 'Accept': 'application/pdf,application/octet-stream;q=0.8'}
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            async def download_one(remote_url: str):
                try:
                    async with semaphore:
                        downloaded = await _download_pdf(
                            session, remote_url, download_dir, max_bytes, allow_private_hosts
                        )
                    return remote_url, downloaded, None
                except Exception as exc:
                    return remote_url, None, str(exc)

            outcomes = await asyncio.gather(*(download_one(url) for url in remote_urls))
            for source, path, error in outcomes:
                if error:
                    errors.append(_pdf_error_result(source, error))
                else:
                    local_jobs.append((source, path))

    deduplicated: Dict[Tuple[str, str], Tuple[str, Path]] = {}
    for source, path in local_jobs:
        deduplicated[(source, str(path).lower())] = (source, path)
    return sorted(deduplicated.values(), key=lambda item: item[0].lower()), errors


def _scan_pdf_jobs(
    jobs: List[Tuple[str, Path]],
    max_bytes: int,
    timeout_seconds: int,
    workers: int,
    max_pages: int,
) -> List[Dict[str, Any]]:
    reports: List[Dict[str, Any]] = []
    worker_count = max(1, min(workers, 16, len(jobs) or 1))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix='pdf-scan') as executor:
        futures = {
            executor.submit(_run_pdf_worker, path, max_bytes, timeout_seconds, max_pages): (source, path)
            for source, path in jobs
        }
        for future in as_completed(futures):
            source, path = futures[future]
            try:
                report = future.result()
            except Exception as exc:
                report = _pdf_error_result(source, f"Unhandled PDF analysis error: {exc}")
            report['source'] = source
            report['local_path'] = str(path) if not source.lower().startswith(('http://', 'https://')) else ""
            reports.append(report)
    return sorted(reports, key=lambda item: str(item.get('source', '')).lower())


def _pdf_report_payload(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts: DefaultDict[str, int] = defaultdict(int)
    for report in reports:
        for item in report.get('evidence', []):
            status_counts[str(item.get('status', 'Unknown'))] += 1
    return {
        "tool": APP_NAME,
        "version": APP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Automated results are evidence for accessibility review and do not establish "
            "WCAG, Section 508, or PDF/UA conformance."
        ),
        "documents": len(reports),
        "documents_with_analysis_errors": sum(1 for report in reports if report.get('error')),
        "status_counts": dict(sorted(status_counts.items())),
        "results": reports,
    }


def _write_pdf_reports(reports: List[Dict[str, Any]], output_dir: Path, formats: Iterable[str]) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _pdf_report_payload(reports)
    requested = {value.strip().lower() for value in formats if value.strip()}
    written: List[Path] = []

    if 'json' in requested:
        path = output_dir / 'pdf-report.json'
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        written.append(path)

    if 'csv' in requested:
        path = output_dir / 'pdf-report.csv'
        fields = [
            'source', 'sha256', 'page_count', 'rule_id', 'criteria', 'title', 'status',
            'severity', 'message', 'evidence', 'remediation', 'analysis_error',
        ]
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for report in reports:
                for item in report.get('evidence', []):
                    row = {
                        'source': report.get('source', ''),
                        'sha256': report.get('sha256', ''),
                        'page_count': report.get('page_count', 0),
                        'rule_id': item.get('rule_id', ''),
                        'criteria': '; '.join(item.get('criteria', [])),
                        'title': item.get('title', ''),
                        'status': item.get('status', ''),
                        'severity': item.get('severity', ''),
                        'message': item.get('message', ''),
                        'evidence': json.dumps(item.get('evidence', {}), ensure_ascii=False, sort_keys=True),
                        'remediation': item.get('remediation', ''),
                        'analysis_error': report.get('error', ''),
                    }
                    writer.writerow({key: _csv_safe(value) for key, value in row.items()})
        written.append(path)

    if 'html' in requested:
        path = output_dir / 'pdf-report.html'
        status_order = {
            EvidenceStatus.FAIL.value: 0,
            EvidenceStatus.ERROR.value: 1,
            EvidenceStatus.NEEDS_REVIEW.value: 2,
            EvidenceStatus.NOT_TESTED.value: 3,
            EvidenceStatus.PASS.value: 4,
            EvidenceStatus.NOT_APPLICABLE.value: 5,
        }
        document_sections: List[str] = []
        for report in reports:
            rows = []
            items = sorted(
                report.get('evidence', []),
                key=lambda item: (status_order.get(item.get('status'), 99), item.get('rule_id', '')),
            )
            for item in items:
                status = str(item.get('status', ''))
                css_class = re.sub(r'[^a-z]+', '-', status.lower()).strip('-')
                rows.append(
                    '<tr>'
                    f'<td><span class="status {css_class}">{html_lib.escape(status)}</span></td>'
                    f'<td><code>{html_lib.escape(str(item.get("rule_id", "")))}</code></td>'
                    f'<td>{html_lib.escape(", ".join(item.get("criteria", [])))}</td>'
                    f'<td><strong>{html_lib.escape(str(item.get("title", "")))}</strong><br>'
                    f'{html_lib.escape(str(item.get("message", "")))}'
                    f'<details><summary>Evidence and remediation</summary><pre>{html_lib.escape(json.dumps(item.get("evidence", {}), indent=2, ensure_ascii=False))}</pre>'
                    f'<p>{html_lib.escape(str(item.get("remediation", "")))}</p></details></td>'
                    '</tr>'
                )
            error_html = (
                f'<p class="error"><strong>Analysis error:</strong> {html_lib.escape(str(report.get("error")))}</p>'
                if report.get('error') else ''
            )
            document_sections.append(
                '<section class="document">'
                f'<h2>{html_lib.escape(str(report.get("source", "")))}</h2>{error_html}'
                f'<p>{report.get("page_count", 0)} page(s), SHA-256 <code>{html_lib.escape(str(report.get("sha256", "")))}</code></p>'
                '<div class="table-wrap"><table><thead><tr><th>Status</th><th>Rule</th><th>WCAG</th><th>Finding</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div></section>'
            )
        html_text = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data: file:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'">
<title>PDF accessibility evidence report</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;max-width:1500px;margin:auto;padding:2rem;color:#172033;background:#f6f8fb}}
h1,h2{{line-height:1.2}} .notice{{background:#eaf3ff;border:1px solid #9bc2f5;padding:1rem;border-radius:.5rem}}
.document{{background:white;margin:1.5rem 0;padding:1.25rem;border-radius:.6rem;box-shadow:0 1px 5px #0002}}
.table-wrap{{overflow:auto}} table{{border-collapse:collapse;width:100%}} th,td{{padding:.65rem;text-align:left;vertical-align:top;border-bottom:1px solid #dde3ec}}
.status{{display:inline-block;padding:.25rem .45rem;border-radius:.3rem;font-weight:650;white-space:nowrap;background:#e7ebf1}}
.fail,.analysis-error{{background:#fee2e2;color:#991b1b}} .needs-manual-review,.not-tested{{background:#fff3cd;color:#6b4f00}}
.pass-narrow-automated-test{{background:#dcfce7;color:#166534}} code,pre{{white-space:pre-wrap;overflow-wrap:anywhere}} .error{{color:#991b1b}}
details{{margin-top:.5rem}}
</style></head><body>
<h1>PDF accessibility evidence report</h1>
<p>Generated by {html_lib.escape(APP_NAME)} {html_lib.escape(APP_VERSION)} on {html_lib.escape(payload['generated_at'])}.</p>
<div class="notice"><strong>Important:</strong> {html_lib.escape(payload['disclaimer'])}</div>
<p>{payload['documents']} document(s); {payload['documents_with_analysis_errors']} with analysis errors.</p>
{"".join(document_sections)}
</body></html>'''
        path.write_text(html_text, encoding='utf-8')
        written.append(path)

    return written


def _same_host_family(url: str, base_hostname: str, include_subdomains: bool) -> bool:
    try:
        hostname = (urlparse(url).hostname or '').lower().rstrip('.')
    except Exception:
        return False
    return hostname == base_hostname or (include_subdomains and hostname.endswith('.' + base_hostname))


def _looks_like_pdf_url(url: str) -> bool:
    return unquote(urlparse(url).path).lower().endswith('.pdf')


async def _discover_pdf_urls(
    start_url: str,
    max_pages: int,
    max_pdfs: int,
    crawl_delay: float,
    include_subdomains: bool,
    allow_private_hosts: bool,
) -> List[str]:
    """Discover PDF links using sitemaps and a bounded same-site crawl."""
    start_url = _validate_http_url(start_url)
    parsed_start = urlparse(start_url)
    base_hostname = (parsed_start.hostname or '').lower().rstrip('.')
    origin = f"{parsed_start.scheme}://{parsed_start.netloc}"
    timeout = aiohttp.ClientTimeout(total=45, connect=10, sock_read=25)
    connector = _safe_connector(allow_private_hosts, limit_per_host=4)
    headers = {'User-Agent': DEFAULT_USER_AGENT, 'Accept': 'text/html,application/xml,text/xml;q=0.9,*/*;q=0.1'}
    pdf_urls: Set[str] = set()
    page_queue: List[str] = [start_url]
    seen_pages: Set[str] = set()

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
        robots = RobotFileParser()
        try:
            async with _safe_get(
                session, urljoin(origin, '/robots.txt'),
                allow_private=allow_private_hosts,
            ) as response:
                if response.status == 200:
                    robots.parse((await _read_capped_text(response, 512 * 1024)).splitlines())
                else:
                    robots.allow_all = True
        except Exception:
            robots.allow_all = True

        sitemap_queue = [urljoin(origin, '/sitemap.xml')]
        seen_sitemaps: Set[str] = set()
        while sitemap_queue and len(seen_sitemaps) < 20 and len(pdf_urls) < max_pdfs:
            sitemap_url = sitemap_queue.pop(0)
            if sitemap_url in seen_sitemaps or not _same_host_family(sitemap_url, base_hostname, include_subdomains):
                continue
            seen_sitemaps.add(sitemap_url)
            try:
                async with _safe_get(
                    session, sitemap_url,
                    allow_private=allow_private_hosts,
                ) as response:
                    if response.status != 200:
                        continue
                    xml_text = await _read_capped_text(response, MAX_SITEMAP_BYTES)
                root = SafeET.fromstring(xml_text)
                locations = [
                    (node.text or '').strip() for node in root.iter()
                    if node.tag.rsplit('}', 1)[-1] == 'loc' and (node.text or '').strip()
                ][:50000]
                is_index = root.tag.rsplit('}', 1)[-1] == 'sitemapindex'
                for location in locations:
                    try:
                        location = _validate_http_url(location)
                    except ValueError:
                        continue
                    if not _same_host_family(location, base_hostname, include_subdomains):
                        continue
                    if _looks_like_pdf_url(location):
                        pdf_urls.add(location)
                    elif is_index and len(sitemap_queue) < 20:
                        sitemap_queue.append(location)
                    elif len(page_queue) < max_pages:
                        page_queue.append(location)
                    if len(pdf_urls) >= max_pdfs:
                        break
            except Exception as exc:
                logger.debug("Sitemap discovery failed for %s: %s", sitemap_url, exc)

        while page_queue and len(seen_pages) < max_pages and len(pdf_urls) < max_pdfs:
            page_url, _ = urldefrag(page_queue.pop(0))
            if page_url in seen_pages or not _same_host_family(page_url, base_hostname, include_subdomains):
                continue
            seen_pages.add(page_url)
            if not robots.can_fetch(DEFAULT_USER_AGENT, page_url):
                continue
            try:
                if crawl_delay:
                    await asyncio.sleep(crawl_delay)
                async with _safe_get(
                    session, page_url,
                    allow_private=allow_private_hosts,
                ) as response:
                    final_url = str(response.url)
                    if response.status >= 400 or not _same_host_family(final_url, base_hostname, include_subdomains):
                        continue
                    if 'html' not in response.headers.get('Content-Type', '').lower():
                        continue
                    content = await _read_capped_text(response)
                soup = BeautifulSoup(content, 'lxml')
                for tag in soup.find_all(['a', 'area'], href=True):
                    candidate, _ = urldefrag(urljoin(final_url, tag.get('href')))
                    try:
                        candidate = _validate_http_url(candidate)
                    except ValueError:
                        continue
                    if not _same_host_family(candidate, base_hostname, include_subdomains):
                        continue
                    if _looks_like_pdf_url(candidate):
                        pdf_urls.add(candidate)
                    elif len(seen_pages) + len(page_queue) < max_pages:
                        page_queue.append(candidate)
                    if len(pdf_urls) >= max_pdfs:
                        break
            except Exception as exc:
                logger.debug("PDF discovery page failed for %s: %s", page_url, exc)

    return sorted(pdf_urls)


# ==============================================================================
# CLI INTERFACE
# ==============================================================================

@click.command('web', context_settings=dict(help_option_names=['-h', '--help']))
@click.version_option(APP_VERSION, '-V', '--version')
@click.argument('target', type=str)
@click.option('--level', '-l', type=click.Choice(['A', 'AA', 'AAA'], case_sensitive=False), default='AA', help='WCAG target level for automated checks.')
@click.option('--output-dir', '-o', type=click.Path(file_okay=False, writable=True), default='a11y_reports', help='Directory to save reports.')
@click.option('--report-formats', default='html,json,csv', help='Comma-separated list of report formats (html,json,csv,md,pdf,junit).')
@click.option('--fix', is_flag=True, default=False, help='Launch review-first local HTML/CSS remediation with diffs, backups, rescanning, and rollback.')
@click.option('--scope', '-s', type=click.Choice(['site', 'folder', 'page'], case_sensitive=False), default=None,
              help="Scan scope for URL targets: 'site' = entire site at any depth; 'folder' = only the target's folder/section; 'page' = the single target page. Defaults to 'site' when --crawl is given, else 'page'.")
@click.option('--crawl', is_flag=True, default=False, help='[Deprecated] Alias for "--scope site". Crawl the entire website from the target URL.')
@click.option('--depth', type=click.IntRange(0, 100000), default=None, help="Optional max-depth cap for 'site'/'folder' scope. Omit for unlimited depth (no matter the depth).")
@click.option('--concurrency', default=10, type=click.IntRange(1, 128), help='Number of concurrent crawler workers/requests (discovery phase).')
@click.option('--analysis-concurrency', type=click.IntRange(1, 64), default=8, help='Pages analyzed in parallel during the analysis phase (Balanced default: 8).')
@click.option('--bulk-mode', type=click.Choice(['tiered', 'full', 'static'], case_sensitive=False), default='tiered',
              help="Large-site strategy: 'tiered' (default) = fast static checks on ALL pages + full browser/axe/dynamic on a per-template sample; 'full' = browser/axe on every page; 'static' = no browser for bulk pages.")
@click.option('--sample-per-template', type=click.IntRange(1, 1000), default=3, help='In tiered mode, how many representative pages per URL-template get the full browser+axe+dynamic pass.')
@click.option('--consistency-sample', type=click.IntRange(0, 1000000), default=1000, help='Max pages sampled for cross-page consistency checks (WCAG 3.2.3/3.2.4/3.2.6).')
@click.option('--dom-mode', type=click.Choice(['none', 'smart', 'all'], case_sensitive=False), default='all',
              help="How fully to apply JS/DOM changes before analyzing sampled pages (mirrors SortSite): 'all' (default, best results) waits for network idle + scrolls to trigger analytics/lazy content; 'smart' waits for network idle; 'none' analyzes the initial DOM.")
@click.option('--wait-network', is_flag=True, default=False, help='Force a network-idle wait even in --dom-mode none (for heavy SPAs).')
@click.option('--validate-html/--no-validate-html', default=True, help='Check HTML standards/validity (deprecated elements, duplicate ids, bad label targets, nested links).')
@click.option('--validate-css/--no-validate-css', default=True, help='Check CSS validity (invalid/unknown properties and syntax errors).')
@click.option('--spell-check/--no-spell-check', default=True, help='Spell-check visible text (requires the optional "pyspellchecker" package).')
@click.option('--spell-lang', default='en', help='Language for spell check (e.g., en, es, fr, de).')
@click.option('--no-sandbox', is_flag=True, default=False, help='[Hardening] Disable the Chromium sandbox. Use ONLY in trusted/containerized environments; weakens isolation.')
@click.option('--allow-private-hosts', is_flag=True, default=False, help='[Hardening] Allow requests to private/loopback/link-local addresses (needed for localhost/intranet scans). Blocked by default to mitigate SSRF.')
@click.option('--axe-script', type=click.Path(exists=True, dir_okay=False), default=None, help='Path to a local axe.min.js to inject instead of the pinned CDN copy (offline/air-gapped use).')
@click.option('--crawl-delay', type=click.FloatRange(0, 3600), default=0.2, help='Politeness delay (seconds) between successive HTTP requests in both crawl and analysis phases.')
@click.option('--max-urls', type=click.IntRange(1, 1000000000), default=None, help='Optional safety cap on unique URLs. Omit for no artificial page-count limit.')
@click.option('--exclude-urls', multiple=True, help='Regex patterns for URLs to exclude from crawling.')
@click.option('--user-agent', default=DEFAULT_USER_AGENT, help='User agent string for crawling and dynamic analysis.')
@click.option('--no-headless', is_flag=True, default=False, help='Run browser in non-headless mode for debugging.')
@click.option('--ci-mode', is_flag=True, default=False, help='Exit with non-zero code if any unfixed critical or serious issues are found.')
@click.option('--wait-selector', type=str, help='Playwright selector to wait for before analysis on dynamic pages.')
@click.option('--verbose', '-v', is_flag=True, default=False, help='Enable verbose logging for debugging.')
def web_command(**kwargs):
    """Scan a website, page, or local HTML content for accessibility issues.

    TARGET: The URL of a website or a path to a local HTML file/directory.

    Examples::

        python WCAG_Site_PDF_Scanner.py web https://example.com --scope site
        python WCAG_Site_PDF_Scanner.py web ./local_site --report-formats html,json
    """
    report_formats = [value.strip().lower() for value in kwargs['report_formats'].split(',') if value.strip()]
    unsupported_formats = sorted(set(report_formats) - {'html', 'json', 'csv', 'md', 'pdf', 'junit'})
    if unsupported_formats:
        raise click.UsageError(f"Unsupported web report format(s): {', '.join(unsupported_formats)}")
    if not report_formats:
        raise click.UsageError("Select at least one web report format.")
    kwargs['report_formats'] = ','.join(dict.fromkeys(report_formats))

    for pattern in kwargs.get('exclude_urls', ()):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise click.UsageError(f"Invalid --exclude-urls regular expression {pattern!r}: {exc}") from exc

    user_agent = kwargs.get('user_agent', '')
    if not user_agent or len(user_agent) > 512 or re.search(r'[\x00-\x1f\x7f]', user_agent):
        raise click.UsageError("--user-agent must be 1 to 512 characters without control characters.")

    if kwargs['verbose']:
        # Update logging level for verbose mode
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream.name == '<stderr>':
                handler.setLevel(logging.DEBUG)

    console.print(Panel(f"[bold cyan]{APP_NAME} v{APP_VERSION}[/bold cyan]", border_style="cyan", expand=False))

    target_parts = urlparse(kwargs['target'])
    is_url_target = target_parts.scheme.lower() in {'http', 'https'}
    if is_url_target and target_parts.scheme != target_parts.scheme.lower():
        kwargs['target'] = target_parts._replace(scheme=target_parts.scheme.lower()).geturl()
    is_local_path = Path(kwargs['target']).exists()

    # Resolve scan scope (new model), keeping --crawl working as a deprecated alias for "site".
    if kwargs.get('scope') is None:
        kwargs['scope'] = 'site' if kwargs.get('crawl') else 'page'
    else:
        kwargs['scope'] = kwargs['scope'].lower()

    if kwargs['fix'] and is_url_target:
        console.print("[bold yellow]Warning:[/bold yellow] `--fix` does not modify remote websites. Scan a local source file or directory to use interactive remediation.")

    if kwargs['scope'] in ('site', 'folder') and is_local_path and not is_url_target:
        console.print(f"[bold yellow]Warning:[/bold yellow] `--scope {kwargs['scope']}` applies to URL targets only. The local file/directory will be scanned as-is.")
        kwargs['scope'] = 'page'

    # SSRF protection is on by default; hint when the target is an obvious private/localhost address.
    if is_url_target and not kwargs.get('allow_private_hosts'):
        _host = (urlparse(kwargs['target']).hostname or '')
        try:
            _private_hint = _host == 'localhost' or _ip_is_blocked(_host, False)
        except ValueError:
            _private_hint = False
        if _private_hint:
            console.print("[yellow]Note:[/yellow] target is a private/localhost address; SSRF protection will block it. "
                          "Re-run with [bold]--allow-private-hosts[/bold] to scan internal addresses.")

    tool = A11yPowerTool(kwargs)
    try:
        asyncio.run(tool.run())
    except KeyboardInterrupt:
        console.print("\n[bold red]Analysis interrupted by user.[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]An unhandled error occurred during analysis: {e}[/bold red]")
        logger.exception("Unhandled error in main execution:")
        sys.exit(1)


def _execute_pdf_scan(
    targets: Tuple[str, ...],
    url_file: Optional[str],
    output_dir: str,
    formats: str,
    workers: int,
    timeout_seconds: int,
    max_download_mb: int,
    max_documents: int,
    max_pages_per_pdf: int,
    allow_private_hosts: bool,
    keep_downloads: bool,
    ci_mode: bool,
) -> List[Dict[str, Any]]:
    if not targets and not url_file:
        raise click.UsageError("Provide at least one PDF path, directory, URL, or --url-file.")
    if any(value <= 0 for value in (
        workers, timeout_seconds, max_download_mb, max_documents, max_pages_per_pdf
    )):
        raise click.UsageError("Workers, timeouts, file limits, document limits, and page limits must be positive.")

    requested_formats = [value.strip().lower() for value in formats.split(',') if value.strip()]
    unsupported = sorted(set(requested_formats) - {'html', 'json', 'csv'})
    if unsupported:
        raise click.UsageError(f"Unsupported PDF report format(s): {', '.join(unsupported)}")
    if not requested_formats:
        raise click.UsageError("Select at least one PDF report format.")

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    _install_quiet_logging(output_path)
    max_bytes = max_download_mb * 1024 * 1024
    temporary_downloads: Optional[tempfile.TemporaryDirectory] = None
    if keep_downloads:
        download_dir = output_path / 'downloaded-pdfs'
    else:
        temporary_downloads = tempfile.TemporaryDirectory(prefix='wcag-pdf-downloads-')
        download_dir = Path(temporary_downloads.name)

    console.print(Panel(
        f"[bold cyan]{APP_NAME} v{APP_VERSION}[/bold cyan]\n"
        "PDF evidence mode uses isolated parser processes and conservative result labels.",
        border_style="cyan",
        expand=False,
    ))
    try:
        jobs, preparation_errors = asyncio.run(_prepare_pdf_jobs(
            targets=targets,
            url_file=url_file,
            download_dir=download_dir,
            max_bytes=max_bytes,
            workers=workers,
            allow_private_hosts=allow_private_hosts,
            max_documents=max_documents,
        ))
        console.print(f"Prepared [bold]{len(jobs)}[/bold] PDF(s) for analysis.")
        scanned = _scan_pdf_jobs(
            jobs, max_bytes, timeout_seconds, workers, max_pages_per_pdf
        ) if jobs else []
        reports = sorted(preparation_errors + scanned, key=lambda item: str(item.get('source', '')).lower())
        written = _write_pdf_reports(reports, output_path, requested_formats)
    finally:
        if temporary_downloads is not None:
            temporary_downloads.cleanup()

    status_counts: DefaultDict[str, int] = defaultdict(int)
    for report in reports:
        for item in report.get('evidence', []):
            status_counts[item.get('status', 'Unknown')] += 1
    summary_table = Table(title="PDF evidence summary")
    summary_table.add_column("Status")
    summary_table.add_column("Count", justify="right")
    for status, count in sorted(status_counts.items()):
        summary_table.add_row(status, str(count))
    console.print(summary_table)
    for path in written:
        console.print(f"Report saved to [cyan]{path}[/cyan]")
    console.print(
        "[yellow]Reminder:[/yellow] These are automated evidence results, not a WCAG, "
        "Section 508, or PDF/UA conformance determination."
    )

    if ci_mode and any(
        item.get('status') in {EvidenceStatus.FAIL.value, EvidenceStatus.ERROR.value}
        for report in reports for item in report.get('evidence', [])
    ):
        raise click.exceptions.Exit(1)
    return reports


@click.command('pdf', context_settings=dict(help_option_names=['-h', '--help']))
@click.argument('targets', nargs=-1)
@click.option('--url-file', type=click.Path(exists=True, dir_okay=False), help='Text file containing one PDF URL per line.')
@click.option('--output-dir', '-o', type=click.Path(file_okay=False, writable=True), default='accessibility_reports', show_default=True)
@click.option('--formats', default='html,json,csv', show_default=True, help='Comma-separated report formats: html,json,csv.')
@click.option('--workers', type=click.IntRange(1, 16), default=4, show_default=True, help='Concurrent isolated PDF workers.')
@click.option('--timeout-seconds', type=click.IntRange(5, 3600), default=120, show_default=True, help='Maximum parser time per PDF.')
@click.option('--max-download-mb', type=click.IntRange(1, 2048), default=100, show_default=True, help='Maximum size of each local or downloaded PDF.')
@click.option('--max-documents', type=click.IntRange(1, 100000), default=DEFAULT_MAX_PDF_DOCUMENTS, show_default=True, help='Maximum PDFs accepted in one run.')
@click.option('--max-pages-per-pdf', type=click.IntRange(1, 1000000), default=DEFAULT_MAX_PDF_PAGES, show_default=True, help='Maximum page count accepted for one PDF.')
@click.option('--allow-private-hosts', is_flag=True, help='Allow PDF URLs on private networks. Off by default for SSRF protection.')
@click.option('--keep-downloads', is_flag=True, help='Retain downloaded PDFs inside the report directory.')
@click.option('--ci-mode', is_flag=True, help='Return failure when an automated failure or analysis error is present.')
def pdf_command(
    targets: Tuple[str, ...],
    url_file: Optional[str],
    output_dir: str,
    formats: str,
    workers: int,
    timeout_seconds: int,
    max_download_mb: int,
    max_documents: int,
    max_pages_per_pdf: int,
    allow_private_hosts: bool,
    keep_downloads: bool,
    ci_mode: bool,
):
    """Scan local PDFs, PDF directories, URLs, or a URL list."""
    _execute_pdf_scan(
        targets, url_file, output_dir, formats, workers, timeout_seconds,
        max_download_mb, max_documents, max_pages_per_pdf,
        allow_private_hosts, keep_downloads, ci_mode,
    )


@click.command('discover-pdfs', context_settings=dict(help_option_names=['-h', '--help']))
@click.argument('start_url')
@click.option('--output', '-o', type=click.Path(dir_okay=False), default='pdf-urls.txt', show_default=True)
@click.option('--max-pages', type=click.IntRange(1, 100000), default=500, show_default=True)
@click.option('--max-pdfs', type=click.IntRange(1, 1000000), default=10000, show_default=True)
@click.option('--crawl-delay', type=click.FloatRange(min=0), default=0.2, show_default=True)
@click.option('--include-subdomains', is_flag=True, help='Include subdomains of the starting hostname.')
@click.option('--allow-private-hosts', is_flag=True, help='Allow private-network discovery targets. Off by default.')
@click.option('--scan', 'scan_after', is_flag=True, help='Scan discovered PDFs immediately after writing the URL list.')
@click.option('--report-dir', type=click.Path(file_okay=False), default='accessibility_reports', show_default=True)
def discover_pdfs_command(
    start_url: str,
    output: str,
    max_pages: int,
    max_pdfs: int,
    crawl_delay: float,
    include_subdomains: bool,
    allow_private_hosts: bool,
    scan_after: bool,
    report_dir: str,
):
    """Discover PDF links using bounded sitemap and same-site crawling."""
    urls = asyncio.run(_discover_pdf_urls(
        start_url, max_pages, max_pdfs, crawl_delay, include_subdomains, allow_private_hosts
    ))
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(''.join(f"{url}\n" for url in urls), encoding='utf-8')
    console.print(f"Discovered [bold]{len(urls)}[/bold] PDF URL(s). List saved to [cyan]{output_path}[/cyan].")
    if scan_after and urls:
        _execute_pdf_scan(
            tuple(), str(output_path), report_dir, 'html,json,csv', 4, 120, 100,
            max_pdfs, DEFAULT_MAX_PDF_PAGES, allow_private_hosts, False, False,
        )


def _run_embedded_self_tests() -> List[Tuple[str, bool, str]]:
    tests: List[Tuple[str, bool, str]] = []

    def record(name: str, action) -> None:
        try:
            result = action()
            tests.append((name, bool(result), "" if result else "returned false"))
        except Exception as exc:
            tests.append((name, False, str(exc)))

    record(
        "Report-link scheme guard",
        lambda: _safe_url('javascript:alert(1)') == '#'
        and _safe_url('https://exa\nmple.com') == '#',
    )
    record("CSV formula neutralization", lambda: _csv_safe('=1+1').startswith("'"))
    record("Cloud metadata address block", lambda: _ip_is_blocked('169.254.169.254', False))
    record("Loopback address block", lambda: _ip_is_blocked('127.0.0.1', False))
    record("AWS IPv6 metadata address block", lambda: _ip_is_blocked('fd00:ec2::254', True))
    record("BCP 47 language validation", lambda: _valid_language_tag('en-US') and not _valid_language_tag('en_US'))
    record(
        "Autocomplete token validation",
        lambda: _valid_autocomplete_value('section-checkout shipping email')
        and not _valid_autocomplete_value('definitely-not-a-field'),
    )
    record(
        "Source-preserving remediation edit",
        lambda: FixerTUI._replace_start_tag_attribute(
            '<img class="hero" src="chart.png">', 'alt', 'Sales & growth'
        ) == '<img class="hero" src="chart.png" alt="Sales &amp; growth">',
    )
    record(
        "Source-template remediation guard",
        lambda: _remediation_template_risk('<main>{{ content }}</main>', '.html') is not None
        and _remediation_template_risk('<main>Static content</main>', '.html') is None,
    )

    def numeric_url_guard_test() -> bool:
        async def exercise() -> bool:
            try:
                await _assert_safe_destination('http://127.0.0.1/test.pdf', False)
            except OSError:
                return True
            return False
        return asyncio.run(exercise())

    record("Numeric loopback URL guard", numeric_url_guard_test)

    def capped_stream_test() -> bool:
        class FakeContent:
            async def iter_chunked(self, _size):
                yield b'alpha'
                yield b'beta'

        class FakeResponse:
            content_length = None
            content = FakeContent()

        return asyncio.run(_read_capped_bytes(FakeResponse(), 9)) == b'alphabeta'

    record("Complete chunked response reads", capped_stream_test)

    def crawler_queue_test() -> bool:
        async def exercise() -> bool:
            report = AccessibilityReport(target='https://example.com')
            crawler = Crawler(
                'https://example.com', 2, 1, [], DEFAULT_USER_AGENT, report,
                0, max_urls_to_crawl=10,
            )
            await crawler._add_to_queue_if_valid('/test', 'https://example.com', 1)
            queued = 'https://example.com/test' in crawler.seen_urls
            fetchable = crawler._is_valid_url('https://example.com/test', check_seen=False)
            return queued and fetchable and crawler.queue.qsize() == 1
        return asyncio.run(exercise())

    record("Crawler queued-versus-processed state", crawler_queue_test)

    def static_html_test() -> bool:
        analyzer = StaticAnalyzer(WCAGLevel.AA)
        issues, _ = analyzer.analyze(
            '<!doctype html><html lang="en"><head><title>Test</title></head><body><img src="x.png"></body></html>',
            'self-test.html',
        )
        return any(issue.criterion == '1.1.1' for issue in issues)

    record("Static HTML missing-alt detection", static_html_test)

    def pdf_test() -> bool:
        try:
            import pikepdf
        except ImportError:
            raise RuntimeError("pikepdf is not installed") from None
        with tempfile.TemporaryDirectory(prefix='wcag-self-test-') as temp_dir:
            path = Path(temp_dir) / 'untagged.pdf'
            with pikepdf.new() as pdf:
                pdf.add_blank_page(page_size=(612, 792))
                pdf.save(path)
            result = _inspect_pdf_in_process(path, 5 * 1024 * 1024)
            return any(
                item['rule_id'] == 'PDF.TAGGED.STRUCTURE' and item['status'] == EvidenceStatus.FAIL.value
                for item in result.get('evidence', [])
            )

    record("PDF untagged-document detection", pdf_test)
    return tests


@click.command('diagnostics', context_settings=dict(help_option_names=['-h', '--help']))
@click.option('--self-test/--no-self-test', default=True, show_default=True, help='Run the offline embedded test suite.')
def diagnostics_command(self_test: bool):
    """Show environment readiness and run safe offline self-tests."""
    table = Table(title=f"{APP_NAME} diagnostics")
    table.add_column("Component")
    table.add_column("Status")
    table.add_row("Python", platform.python_version())
    packages = ['aiohttp', 'beautifulsoup4', 'click', 'cssutils', 'pikepdf', 'playwright', 'rich']
    for package in packages:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "Not installed"
        table.add_row(package, version)
    table.add_row("Playwright import", "Ready" if PLAYWRIGHT_READY else "Unavailable")
    table.add_row("NLTK data", "Ready" if NLTK_READY else "Optional data not installed")
    console.print(table)

    if self_test:
        results = _run_embedded_self_tests()
        test_table = Table(title="Offline self-tests")
        test_table.add_column("Test")
        test_table.add_column("Result")
        test_table.add_column("Detail")
        for name, passed, detail in results:
            test_table.add_row(name, "PASS" if passed else "FAIL", detail)
        console.print(test_table)
        if not all(passed for _, passed, _ in results):
            raise click.exceptions.Exit(1)


@click.command('_pdf-worker', hidden=True)
@click.argument('pdf_path', type=click.Path(exists=True, dir_okay=False))
@click.option('--max-file-bytes', type=click.IntRange(1), required=True)
@click.option('--max-pages', type=click.IntRange(1), required=True)
def pdf_worker_command(pdf_path: str, max_file_bytes: int, max_pages: int):
    """Internal isolated PDF worker."""
    click.echo(json.dumps(
        _inspect_pdf_in_process(Path(pdf_path), max_file_bytes, max_pages),
        ensure_ascii=False,
    ))


def _interactive_menu(ctx: click.Context) -> None:
    console.print(Panel(
        f"[bold cyan]{APP_NAME} v{APP_VERSION}[/bold cyan]\n"
        "Website, local HTML, and PDF accessibility evidence in one script.",
        border_style="cyan",
        expand=False,
    ))
    choices = [
        "Scan a website, page, or local HTML",
        "Scan PDF files, a directory, or URLs",
        "Discover PDFs from a website",
        "Run diagnostics and self-tests",
        "Exit",
    ]
    selection = questionary.select("Choose an action", choices=choices).ask()
    if not selection or selection == "Exit":
        return
    if selection.startswith("Scan a website"):
        target = questionary.text("Website URL, HTML file, or directory").ask()
        if not target:
            return
        scope = 'page'
        if str(target).lower().startswith(('http://', 'https://')):
            scope = questionary.select("Scan scope", choices=['page', 'folder', 'site'], default='page').ask() or 'page'
        ctx.invoke(web_command, target=target, scope=scope)
    elif selection.startswith("Scan PDF"):
        target = questionary.text("PDF path, directory, or URL").ask()
        if target:
            ctx.invoke(pdf_command, targets=(target,))
    elif selection.startswith("Discover"):
        start_url = questionary.text("Starting website URL").ask()
        if start_url:
            scan_after = bool(questionary.confirm("Scan discovered PDFs immediately?", default=False).ask())
            ctx.invoke(discover_pdfs_command, start_url=start_url, scan_after=scan_after)
    else:
        ctx.invoke(diagnostics_command)


@click.group(invoke_without_command=True, no_args_is_help=False, context_settings=dict(help_option_names=['-h', '--help']))
@click.version_option(APP_VERSION, '-V', '--version')
@click.pass_context
def cli(ctx: click.Context):
    """One-file WCAG 2.2 website, HTML, and PDF accessibility scanner."""
    if ctx.invoked_subcommand is None:
        _interactive_menu(ctx)


cli.add_command(web_command)
cli.add_command(pdf_command)
cli.add_command(discover_pdfs_command)
cli.add_command(diagnostics_command)
cli.add_command(pdf_worker_command)


def main() -> None:
    args = sys.argv[1:]
    commands = {'web', 'pdf', 'discover-pdfs', 'diagnostics', '_pdf-worker'}
    if args and args[0] not in commands and not args[0].startswith('-'):
        candidate = args[0]
        candidate_path = Path(candidate)
        use_pdf = _looks_like_pdf_url(candidate) if candidate.lower().startswith(('http://', 'https://')) else (
            candidate_path.is_file() and candidate_path.suffix.lower() == '.pdf'
        )
        args.insert(0, 'pdf' if use_pdf else 'web')
    cli.main(args=args, prog_name='WCAG_Site_PDF_Scanner.py')


if __name__ == '__main__':
    main()
