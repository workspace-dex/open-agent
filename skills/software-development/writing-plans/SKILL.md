---
name: writing-plans
description: "Create actionable implementation plans: break down complex tasks into executable steps."
version: 1.0.0
author: Open-Agent
triggers:
  - plan
  - implement
  - build
  - create
  - roadmap
  - steps
  - how to
  - outline
  - breakdown
metadata:
  hermes:
    tags: [planning, implementation, roadmaps, task-breakdown]
    related_skills: [systematic-debugging, test-driven-development]
---

# Writing Implementation Plans

## Core Principle

**Every complex task needs a plan. Plans should be executable, not theoretical.**

## The Planning Process

### 1. Understand the Goal

- What's the desired outcome?
- What does success look like?
- What's the simplest version that works?

### 2. Identify Constraints

- Time limits?
- Budget/Resource limits?
- Technical constraints?
- Dependencies?

### 3. Break It Down

**Rule: Each step should be:**
- Executable in 1-2 hours
- Testable/verifiable
- Independent (can start without waiting)

### 4. Order Dependencies

- What must happen first?
- What's blocking what?
- What's parallelizable?

### 5. Identify Risks

- What could go wrong?
- What's the fallback?
- What's the simplest path?

## Plan Format

```
## Goal: [One sentence]

## Steps

1. **[Step Name]**
   - What: [Description]
   - Verify: [How to confirm it worked]
   - Time: [Estimate]

2. **[Step Name]**
   - ...

## Risks & Mitigations
- Risk: [What]
- Mitigation: [How]
```

## Execution Rules

- Start with the smallest working version
- Iterate, don't perfect upfront
- Test each step before moving on
- Re-plan if blockers emerge
