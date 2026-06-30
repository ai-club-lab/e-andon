# TDD Workflow Skill

Test-Driven Development workflow for reliable code.

## The Cycle

```
RED → GREEN → REFACTOR → REPEAT
```

## Phase 1: RED - Write Failing Test

### Define the Interface First
```typescript
interface OrderService {
  createOrder(items: OrderItem[]): Promise<Order>;
  getOrder(id: string): Promise<Order | null>;
  cancelOrder(id: string): Promise<void>;
}
```

### Write the Test
```typescript
describe('OrderService', () => {
  describe('createOrder', () => {
    it('should create order with valid items', async () => {
      const items = [{ productId: '1', quantity: 2 }];

      const order = await service.createOrder(items);

      expect(order.id).toBeDefined();
      expect(order.items).toHaveLength(1);
      expect(order.status).toBe('pending');
    });

    it('should throw when items array is empty', async () => {
      await expect(service.createOrder([]))
        .rejects.toThrow(ValidationError);
    });
  });
});
```

### Verify Test Fails
Run tests - they should fail because implementation doesn't exist.

## Phase 2: GREEN - Minimal Implementation

Write just enough code to make the test pass:

```typescript
class OrderServiceImpl implements OrderService {
  async createOrder(items: OrderItem[]): Promise<Order> {
    if (items.length === 0) {
      throw new ValidationError('Items cannot be empty');
    }

    return {
      id: generateId(),
      items,
      status: 'pending',
      createdAt: new Date(),
    };
  }
}
```

### Run Tests Again
All tests should pass. Don't optimize yet.

## Phase 3: REFACTOR - Improve

With green tests as safety net:
- Extract helper functions
- Improve naming
- Remove duplication
- Add missing error handling

Run tests after each change.

## Guidelines

### Test Naming
```
should [expected behavior] when [condition]
```

### Test Structure (AAA)
```typescript
it('should calculate total with discount', () => {
  // Arrange
  const order = createOrder({ subtotal: 100 });
  const discount = { percent: 10 };

  // Act
  const total = calculateTotal(order, discount);

  // Assert
  expect(total).toBe(90);
});
```

### Coverage Target
- Aim for 80%+ on new code
- Focus on behavior coverage, not line coverage
- Critical paths need 100%

### What to Mock
- External services (APIs, databases)
- Time-dependent functions
- Random generators
- File system operations

### What NOT to Mock
- The unit under test
- Simple value objects
- Pure functions with no side effects

## Verification Checklist
- [ ] All tests pass
- [ ] Coverage meets threshold
- [ ] No type errors (`tsc --noEmit`)
- [ ] Linting passes
- [ ] Tests are readable and maintainable
