---
name: issue-template-validation
description: Validate GitHub issue template sections before creating an issue. Invoke this skill immediately before any `gh issue create` command. Do not proceed with issue creation if any PRESENT required section is empty, contains placeholders (TBD/TDB/TODO/FIXME/WIP), or duplicates another section. Detect the template type from the issue title prefix or user context and apply the matching template rules.
---

# Issue Template Validation

## When to Use This Skill

Invoke this skill **before creating any GitHub issue** to validate that all required sections contain meaningful, unique content.

## Template Types

Detect the template from the issue title prefix or user context:

| Prefix | Template |
|---|---|
| `[Bug]:` | Bug Report |
| `[Feature]:` | Feature Request |
| `[Task]:` | Task |
| `[Docs]:` | Documentation |
| `[Question]:` | Question |

If no prefix is present, infer from the content or ask the user which template they intend to use.

## Required Sections per Template

### 🐛 Bug Report

**Required (must have meaningful content):**
1. `:bug: Bug Description`
2. `✅ Expected Behavior`
3. `❌ Actual Behavior`
4. `🔁 Steps to Reproduce`

**Optional (skip validation if header absent):**
- `🖥️ Environment`
- `📸 Screenshots / Logs`
- `🧠 Additional Context`

---

### ✨ Feature Request

**Required:**
1. `🚀 Feature Summary`
2. `🤔 Problem Statement`
3. `💡 Proposed Solution`

**Optional:**
- `🔄 Alternatives Considered`
- `📈 Impact`
- `📝 Additional Context`

---

### 📋 Task

**Required:**
1. `📌 Summary`
2. `🎯 Objectives`
3. `🧩 Scope`

**Optional:**
- `🧠 Design / Decisions`
- `📅 Timeline`
- `✅ Definition of Done`
- `📣 Notes / Updates`

---

### 📚 Documentation

**Required:**
1. `📍 Location`
2. `❓ What's Wrong`
3. `✅ Suggested Improvement`

**Optional:**
- `🧠 Additional Context`

---

### ❓ Question

**Required:**
1. `❓ Question`

**Optional:**
- `🔍 What I've Tried`
- `🧾 Context`
- `📎 Additional Info`

---

## Validation Rules

**Important distinction:**
- **Missing optional section** (header not in body) = ALLOWED
- **Missing required section** (header not in body) = FAIL — ask user to add it
- **Empty section** (header present but no content) = FAIL — user must fill it or remove it

For each **required** section that IS present, and for each **optional** section that IS present, validation **FAILS** if:

### 1. Empty Content
Section header exists but contains only whitespace or placeholder list items.

**Action:** Reject and ask user to either add substantive content or remove the section header entirely.

### 2. Placeholder Text
Case-insensitive detection of: `TBD`, `TDB`, `TODO`, `FIXME`, `WIP`

This includes placeholders inside list items (e.g. `- TBD`, `- [ ] TDB`).

**Action:** Reject and ask user to replace with actual content.

### 3. Content Duplication
Section content is an exact or near-exact duplicate of another section.

**Action:** Reject and explain each section must articulate a distinct aspect.

### 4. Missing Required Section
A required section for the detected template type is entirely absent from the body.

**Action:** Reject and ask user to add the missing section with real content.

---

## What Counts as Meaningful Content

**Valid examples:**

- **Bug Description:** "When running `ensemble run --parallel`, jobs are silently dropped after the 10th concurrent execution. No error is logged."
- **Expected Behavior:** "All submitted jobs should be executed. The system should queue excess jobs if concurrency limit is hit."
- **Objectives:** "- [ ] Identify the root cause of the queue overflow\n- [ ] Add backpressure mechanism\n- [ ] Add test coverage for high-concurrency scenarios"
- **Proposed Solution:** "Add a configurable `max_queue_size` parameter to the worker executor with exponential backoff when the queue is full."

**Invalid examples:**

- `TBD`, `TDB`, `TODO`, `FIXME`, `WIP`
- `- TBD` or `- [ ] TDB`
- Empty section with just the header
- Copy of another section's content

---

## Validation Workflow

1. **Detect template type** from title prefix or context (ask if unclear)
2. **Check required sections** are all present in the body → fail if any are missing
3. **For each present section** (required or optional), validate:
   - Trim whitespace
   - Check if empty → FAIL
   - Check for placeholder patterns (case-insensitive) → FAIL
   - Check for duplicate content → FAIL
4. **If ANY validation fails:**
   - Do **not** proceed with issue creation
   - Report all failures with specific reasons
   - Ask user to revise and retry
5. **If ALL validations pass:**
   - Proceed with issue creation using `gh issue create`

---

## Error Reporting Format

```
❌ Issue validation failed. Will not create issue until these sections are fixed:

1. "🎯 Objectives" [required]
   ❌ Contains placeholder text: TDB
   → Replace with actual objectives as a checklist

2. "🧩 Scope" [required]
   ❌ Section header exists but is empty
   → Describe what is in and out of scope, or remove the header

3. "🚀 Feature Summary" [required]
   ❌ Section is missing from the issue body
   → Add this section with a short description of the feature

Please revise these sections and try creating the issue again.
```

---

## Implementation Notes

- Placeholder detection is case-insensitive and covers bare words as well as list items (`- TBD`, `- [ ] TDB`)
- Trim whitespace before checking for empty content
- Identify sections by emoji/heading markers from the templates
- This validation does NOT apply to issue updates after initial creation or issues created manually outside Claude