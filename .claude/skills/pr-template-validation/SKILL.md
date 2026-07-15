---
name: pr-template-validation
description: Validate PR body sections before creating a GitHub PR. Invoke this skill immediately before any `gh pr create` command. Do not proceed with PR creation if any PRESENT section is empty, contains placeholders (TBD/TODO/FIXME/WIP), or duplicates another section. Missing sections are allowed if not relevant.
---

# PR Template Validation

## When to Use This Skill

Invoke this skill **before creating any GitHub PR** to validate that all PR template sections contain meaningful, unique content.

## Template Sections to Validate

Validate these sections from `.github/PULL_REQUEST_TEMPLATE.md`:

1. **🧑‍💻 What is the change being made?**
2. **:question: Why is the change being made?**
3. **:white_check_mark: How has this been tested?**
4. **:books: How has this been documented?**
5. **:link: Related Issues**

## Validation Rules

**Important distinction:**
- **Missing section** (header not included in PR body) = ALLOWED if not relevant to the change
- **Empty section** (header exists but no content) = FAIL (user must either fill it or remove it)

For each section that IS present in the PR body, validation **FAILS** if:

### 1. Empty Content
- Section header exists but contains only whitespace or is completely empty
- **Action:** Reject and ask user to either:
  - Add substantive content, OR
  - Remove the section header entirely if not relevant

### 2. Placeholder Text Detection
Case-insensitive detection of:
- `TBD` (To Be Determined)
- `TODO` (To Do)
- `FIXME` (Fix Me)
- `WIP` (Work In Progress)

**Example failure:** "How has this been tested?" contains "TBD - will test later"

**Action:** Reject and ask user to replace placeholder with actual content

### 3. Content Duplication
If a section's content is an exact or near-exact duplicate of another section.

**Example failure:**
- "What is the change?" = "Add user authentication"
- "Why is the change?" = "Add user authentication"

**Action:** Reject and explain that each section must articulate a unique aspect (What vs Why vs How)

## What Counts as Meaningful Content

**Valid examples:**

- **What:** "Add JWT token refresh mechanism to extend session lifetime without requiring re-login. Implements automatic token rotation with 5-minute intervals."

- **Why:** "Users were experiencing session timeouts during long workflows. This allows sessions to stay active without interrupting user work."

- **Testing:** "Added unit tests for token rotation logic. Verified refresh tokens are generated and validated correctly. Manual testing in staging confirms token expiry and refresh behavior."

- **Documentation:** "Updated API docs with new `/auth/refresh` endpoint. Added troubleshooting section for token expiration in runbook."

- **Related Issues:** "Closes #1234. Related to #1235 for session management improvements."

**Invalid examples:**

- **Placeholder:** "TBD" or "Will add tests later"
- **Dismissive:** "N/A" or "Not applicable"
- **Duplicate:** Copy of another section's content
- **Too vague:** "Added stuff" or "Fixed bugs"

## Validation Workflow

When invoked during PR creation:

1. **Extract** the PR body content
2. **Parse** to identify which template sections are present (by emoji/heading markers)
3. **For each PRESENT section**, validate:
   - Trim whitespace
   - Check if empty (header exists but no content) → FAIL
   - Check for placeholder patterns (case-insensitive) → FAIL
   - Check for duplicate content across other sections → FAIL
   - **Note:** If section header is missing entirely, skip validation (allowed if not relevant)
4. **If ANY validation fails:**
   - **Do not proceed** with PR creation
   - Report failures with specific reasons
   - Provide examples of valid content
   - Ask user to revise and retry
5. **If ALL validations pass:**
   - Proceed with PR creation

## Error Reporting Format

When validation fails, report like this:

```
❌ PR validation failed. Will not create PR until these sections are fixed:

1. "Why is the change being made?"
   ❌ Contains placeholder text: TBD
   → Replace with actual reasoning for this change

2. "How has this been tested?"
   ❌ Section header exists but is empty
   → Either add testing details, or remove the section header if no testing was needed

3. "How has this been documented?"
   ❌ Content duplicates "What is the change being made?"
   → Explain what docs were updated, not just repeat the change

Please revise these sections and try creating the PR again.
```

## Implementation Notes

- Validation is case-insensitive for placeholder detection
- Trim all whitespace before checking for empty content
- Identify sections by emoji headers from the template
- Consider section separators (blank lines) as structural markers
- This validation does NOT apply to PR updates after initial creation or manual PRs created outside Claude
