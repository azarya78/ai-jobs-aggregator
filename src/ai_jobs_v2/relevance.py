"""AI Evaluation & Related Freelance Relevance Classifier (Title + Full Description)."""
from __future__ import annotations
import re
from typing import List, Tuple
from .models import RawJobListing, RelevanceResult

CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    (
        "Indonesian Language AI Evaluation",
        [
            r"\bindonesian\b.*\b(rater|evaluat|annotat|ai\b|llm|linguist|voice|audio|tutor|trainer|writer|grading|qa|generalist|bilingual|expert)\b",
            r"\bbahasa\s+indonesia\b.*\b(ai\b|model|evaluat|peringkat|penulis|annotat|voice|percakapan|latih|melatih)\b",
            r"\b(rater|evaluat|annotat|ai\s+trainer|ai\s+tutor|linguist|bilingual)\b.*\bindonesian\b",
        ],
    ),
    (
        "Search Quality Rating",
        [
            r"\bsearch\s+(quality\s+|relevance\s+)?(rater|evaluat|rating|annotat)\b",
            r"\bsearch\s+engine\s+evaluat",
            r"\bonline\s+data\s+analyst\b",
            r"\bpersonalized\s+internet\s+assessor\b",
            r"\binternet\s+(safety\s+)?(evaluat|assessor|analyst)\b",
            r"\bad(s)?\s+(quality\s+)?(rater|evaluat|assessor)",
            r"\bmaps?\s+(quality\s+|search\s+)?(evaluat|analyst|rater)\b",
            r"\bgrade\s+how\s+useful\s+and\s+precise\s+users['']?\s+queries\b",
        ],
    ),
    (
        "Preference Ranking / Side-by-Side Evaluation",
        [
            r"\bpreference\s+ranking\b",
            r"\bside[- ]by[- ]side\b",
            r"\bsxs\s+evaluat",
            r"\bpairwise\s+comparison\b",
            r"\brlhf\b",
            r"\brank(ing)?\s+(a\s+series\s+of\s+)?(ai|model|llm)\s+(responses|outputs)\b",
            r"\bmemberi\s+peringkat\s+pada\s+serangkaian\s+respons\b",
        ],
    ),
    (
        "Factuality & Instruction Following Assessment",
        [
            r"\bfactuality\b",
            r"\binstruction[- ]following\b",
            r"\bhallucination\b",
            r"\brubric\s+auditor\b",
            r"\bdeep\s+research\s+reviewer\b",
            r"\bakurat\s+secara\s+fakta\b",
            r"\bflagging\s+where\s+a\s+model\s+is\s+confidently\s+wrong\b",
        ],
    ),
    (
        "TTS Evaluation",
        [
            r"\btts\b",
            r"\btext[- ]to[- ]speech\b",
            r"\bvoice\s+cloning\b",
            r"\bprosody\s+evaluat",
        ],
    ),
    (
        "Audio & Speech Evaluation",
        [
            r"\b(audio|speech|voice)\s+(quality\s+|conversation\s+)?(evaluat|rater|qa|annotat|transcri|collect|recording\s+contributor|recording\s+specialist)\b",
            r"\bpronunciation\s+assessment\s+annotat",
            r"\basr[- ]generated\s+transcripts\b",
            r"\baudio\s+artifacts\b",
            r"\btrain\s+generative\s+ai\s+voice\s+models\b",
        ],
    ),
    (
        "Multimodal AI Evaluation",
        [
            r"\bmultimodal\b.*\b(evaluat|annotat|ai|data|model)\b",
            r"\bvision[- ]language\b",
            r"\bvlm\b.*\b(annotat|evaluat|data)\b",
            r"\bvideo\s+and\s+(?:audio|image)\s+(?:localization\s+)?(?:quality\s+)?(rater|qa|evaluat)",
        ],
    ),
    (
        "Image & Video Evaluation",
        [
            r"\b(image|video)\s+(quality\s+)?(evaluat|rater|qc|qa|annotat|labeling)\b",
            r"\bvisual\s+storytelling\s+prompt\b",
            r"\bannotate\s+short\s+video\s+clips\b",
        ],
    ),
    (
        "AI Grading",
        [
            r"\b(ai|app|model|output|response)\s+grading\b",
            r"\bgrading\s+model\s+output\b",
        ],
    ),
    (
        "LLM Evaluation",
        [
            r"\bllm\s+(?:evaluat|prompt\s+author)",
            r"\bai\s+response\s+evaluat",
            r"\bprompt\s*(&|and)\s*response\s+(evaluat|creation)\b",
            r"\bai\s+chatbot\s+(tester|evaluat)",
            r"\bconversation\s+quality\s+analyst\b",
            r"\bai\s+red\s+team\b",
            r"\bred[- ]teaming\b",
            r"\bai\s+safety\b.*\b(expert|evaluat|trainer|generalist)\b",
            r"\bevaluate\s+ai[- ](?:generated\s+)?(outputs|responses|writing|text|conversations)\b",
            r"\brate\s+responses\s+from\s+cutting[- ]edge\s+chatbots\b",
        ],
    ),
    (
        "AI Tutor / AI Trainer",
        [
            r"\bai\s+(tutor|trainer)s?\b",
            r"\b(sme|domain)\s+task\s+(creator|author)\b",
            r"\btask\s+author\s*(?:\(ai\s+training\))?\b",
            r"\bprompt\s+writer\b",
            r"\btrain(ing)?\s+(and\s+(?:evaluating|enhancing)\s+)?(next[- ]generation\s+|frontier\s+|groundbreaking\s+|advanced\s+)?(?:ai\s+)?(models|systems|llms)\b",
            r"\bcreate\s+realistic\s+work\s+tasks\s+and\s+materials\s+for\s+ai\s+training\b",
        ],
    ),
    (
        "Linguistic Evaluation & Translation Quality",
        [
            r"\blinguistic\s+(ai\s+)?(evaluat|auditor|qa|specialist|expert)s?\b",
            r"\btranslation\s+(?:quality|evaluat)\b",
            r"\bmachine\s+translation\s+post[- ]edit",
            r"\bevaluate\s+machine\s+translated\s+text\b",
            r"\b(linguist|language\s+expert|bilingual\s+.*(?:expert|generalist))\b.*\b(ai|llm|machine\s+translation|train|evaluat|annotat)\b",
        ],
    ),
    (
        "AI Quality Assurance",
        [
            r"\bai\s+(data\s+)?quality\s+(analyst|rater|assurance|specialist)\b",
            r"\bcontent\s+reviewer\b.*\b(ai|research\s+labs|evaluate)\b",
        ],
    ),
    (
        "Text Annotation",
        [
            r"\btext\s+annotat",
            r"\bdocument\s+annotation\b",
        ],
    ),
    (
        "Data Annotation",
        [
            r"\b(gen\s*ai\s+|ai\s+)?data\s+(annotat|label(ing|er)|specialist)\b",
            r"\bannotat(e|ing)\s+data\s*sets\b",
            r"\bdata\s+annotation\b",
        ],
    ),
]

CORPORATE_TITLE_EXCLUSIONS = [
    r"\b(senior|staff|principal|lead|founding|forward\s+deployed|platform|backend|frontend|full[- ]stack|devops|mlops|site\s+reliability|infrastructure|ios|android)\s+(software\s+)?engineer\b",
    r"\bsoftware\s+engineer\b",
    r"\bengineering\s+manager\b",
    r"\bmember\s+of\s+technical\s+staff\b",
    r"\b(vp|vice\s+president|head\s+of|chief|director)\b",
    r"\b(account\s+executive|business\s+development|sales\s+enablement|sales\s+representative|sales\s+agents?)\b",
    r"\b(marketing\s+manager|performance\s+marketing|content\s+marketer|growth\s+marketer)\b",
    r"\b(recruiter|talent\s+acquisition|sourcer|people\s+operations|hr\s+manager)\b",
    r"\b(it\s+manager|corporate\s+it|legal\s+counsel|general\s+counsel|payroll|controller|accountant)\b",
    r"\b(project\s+manager|program\s+manager|project\s+maanger|strategic\s+projects?\s+lead|engagement\s+manager|founder['']?s\s+office)\b",
    r"\b(cuda|gpu\s+programming|kernel)\b.*\bengineer\b",
]

NON_AI_EVAL_STUDY_EXCLUSIONS = [
    r"\b(sharing\s+your\s+fitness\s+data|youth\s+study|clinical\s+trial)\b",
    r"\b(email\s+(?:data\s+)?collection|share\s+order\s+and\s+delivery\s+emails)\b",
    r"\b(indoor\s+3d\s+home\s+capture|movement\s+recording\s+study)\b",
]

SKILL_PATTERNS = [
    ("Indonesian", r"\b(indonesian|bahasa\s+indonesia)\b"),
    ("LLM Evaluation", r"\b(llm|large\s+language\s+model|chatbot)\b"),
    ("RLHF", r"\b(rlhf|preference\s+ranking|side[- ]by[- ]side)\b"),
    ("Search Rating", r"\b(search\s+quality|search\s+relevance|search\s+engine|queries)\b"),
    ("Factuality", r"\b(factuality|hallucination|rubric)\b"),
    ("Audio / Speech", r"\b(audio|speech|voice|asr|tts)\b"),
    ("Multimodal / Video", r"\b(video|image|multimodal|vlm)\b"),
    ("Data Annotation", r"\b(annotation|labeling|tagging)\b"),
    ("Prompt Writing", r"\b(prompt\s+writing|prompt\s+authoring|prompt\s+engineering|prompts)\b"),
    ("Translation / Linguistics", r"\b(linguist|translation|bilingual)\b"),
]


def classify_relevance(raw: RawJobListing) -> RelevanceResult:
    title = (raw.title or "").strip()
    desc = (raw.description_text or "").strip()
    dept = (raw.department or "").strip()
    emp_type = (raw.employment_type or "").strip().lower()
    combined = f"{title}\n{dept}\n{desc}"

    if not title:
        return RelevanceResult(is_relevant=False, category="Irrelevant", rejection_reason="empty_title")

    has_explicit_eval_title = bool(
        re.search(
            r"\b(ai\s+trainer|ai\s+tutor|evaluator|rater|annotator|task\s+author|task\s+creator|prompt\s+writer|rubric\s+auditor|online\s+data\s+analyst|internet\s+assessor)\b",
            title,
            re.IGNORECASE,
        )
    )
    if not has_explicit_eval_title:
        for pat in CORPORATE_TITLE_EXCLUSIONS:
            if re.search(pat, title, re.IGNORECASE):
                return RelevanceResult(
                    is_relevant=False,
                    category="Rejected Corporate/Engineering",
                    rejection_reason=f"excluded_corporate_title:{pat}",
                )

    if re.search(r"\b(gtm|sales\s*&\s*solutions|human\s+resource|people\s+experience|corporate\s+it)\b", dept, re.IGNORECASE):
        return RelevanceResult(
            is_relevant=False,
            category="Rejected Corporate Department",
            rejection_reason=f"excluded_department:{dept}",
        )

    if (
        "full time" in emp_type or "full-time" in emp_type
    ) and re.search(r"\b(401\(k\)|offers\s+equity|commuting\s+support|unlimited\s+pto)\b", desc, re.IGNORECASE):
        return RelevanceResult(
            is_relevant=False,
            category="Rejected Full-Time Salaried Staff",
            rejection_reason="salaried_equity_staff_role",
        )

    for study_pat in NON_AI_EVAL_STUDY_EXCLUSIONS:
        if re.search(study_pat, combined, re.IGNORECASE):
            return RelevanceResult(
                is_relevant=False,
                category="Rejected Non-AI Study",
                rejection_reason="non_ai_consumer_study",
            )

    matched_categories: List[str] = []
    matched_signals: List[str] = []
    for category_name, patterns in CATEGORY_RULES:
        for pat in patterns:
            m = re.search(pat, combined, re.IGNORECASE | re.DOTALL)
            if m:
                matched_categories.append(category_name)
                snippet = re.sub(r"\s+", " ", m.group(0))[:80]
                matched_signals.append(f"{category_name}:{snippet}")
                break

    extracted_skills = [
        label for label, spat in SKILL_PATTERNS if re.search(spat, combined, re.IGNORECASE)
    ]

    if matched_categories:
        primary_category = matched_categories[0]
        return RelevanceResult(
            is_relevant=True,
            category=primary_category,
            matched_signals=matched_signals[:4],
            extracted_skills=extracted_skills[:6],
        )

    if raw.pure_ai_eval_board and re.search(
        r"\b(evaluate|annotate|train|rating|rubric|prompt|ai\s+model|machine\s+learning|quality\s+check)\b",
        combined,
        re.IGNORECASE,
    ):
        return RelevanceResult(
            is_relevant=True,
            category="AI Training",
            matched_signals=["pure_ai_board_description_signal"],
            extracted_skills=extracted_skills[:6] or ["AI Training"],
        )

    return RelevanceResult(
        is_relevant=False,
        category="Irrelevant",
        rejection_reason="no_ai_evaluation_signals",
    )
