export function ruleExpressionForDisplay(expression: string, labels: Record<string, string>): string {
  return expression.replace(/\b[A-Za-z_][A-Za-z0-9_]*\b/g, (token) => labels[token] || token)
}
