---
name: systematic-debugging
description: "4-phase root cause debugging: understand bugs before fixing. Never propose fixes without root cause investigation."
version: 1.0.0
author: Open-Agent
triggers:
  - bug
  - fix
  - error
  - fails
  - exception
  - traceback
  - debugging
  - troubleshoot
  - issue
metadata:
  hermes:
    tags: [debugging, troubleshooting, problem-solving, root-cause, investigation]
    related_skills: [test-driven-development, writing-plans]
---

# Systematic Debugging

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

- Test failures
- Bugs in production  
- Unexpected behavior
- Performance problems
- Build failures

## The Four Phases

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully**
   - Don't skip past errors or warnings
   - They often contain the exact solution
   - Read stack traces completely

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - What are the exact steps?
   - If not reproducible → gather more data

3. **Form Hypothesis**
   - What's the expected behavior?
   - What's actually happening?
   - What's the simplest explanation?

### Phase 2: Isolate

- Create minimal reproduction case
- Remove unrelated variables
- Test in isolation

### Phase 3: Fix

- Fix the ROOT CAUSE, not symptoms
- Verify the fix works
- Ensure no regressions

### Phase 4: Prevent

- Add test to catch recurrence
- Document the learning
- Consider related areas

## Execution Rules

- NEVER skip to "just fix it" 
- Every bug has a root cause
- Quick patches create technical debt
- Systematic is faster than thrashing
