---
name: test-driven-development
description: "Red-Green-Refactor: write tests first, then code to make them pass."
version: 1.0.0
author: Open-Agent
triggers:
  - test
  - tdd
  - unit test
  - pytest
  - assert
  - coverage
  - spec
  - behavior
metadata:
  hermes:
    tags: [testing, tdd, quality, code-design]
    related_skills: [systematic-debugging, writing-plans]
---

# Test-Driven Development

## The Cycle

```
1. RED    → Write failing test
2. GREEN  → Write minimal code to pass
3. REFACTOR → Clean up while keeping tests green
```

## When to Use TDD

- **New features** — define behavior upfront
- **Bug fixes** — write test that reproduces bug first
- **Refactoring** — ensure behavior preserved
- **API design** — tests as spec

## How to Write Tests

### 1. Name Clearly

```python
def test_user_can_login_with_valid_credentials():
    ...

def test_login_fails_with_wrong_password():
    ...
```

### 2. Test One Thing

- Each test = one behavior
- No multiple assertions that could fail for different reasons

### 3. AAA Pattern

```python
def test_something():
    # Arrange
    setup_state()
    
    # Act
    result = do_something()
    
    # Assert
    assert result == expected
```

### 4. Meaningful Assertions

```python
# Bad
assert user.is_valid()

# Good  
assert user.has_permission("read")
assert user.email_verified == True
```

## Common Mistakes

- Writing tests after code (not TDD)
- Testing implementation details
- Over-mocking
- Tests that depend on each other
- No edge case coverage

## Quick Wins

- Start with edge cases: empty, null, zero
- Test error paths
- Test boundary conditions
