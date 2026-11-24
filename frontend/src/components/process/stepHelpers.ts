export function stepBlocker(...conditions: Array<[unknown, string]>): string {
  return conditions.find(([blocked]) => Boolean(blocked))?.[1] || '';
}
