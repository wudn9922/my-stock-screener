/** Preserve the identities already used by inline research charts and their saved drawings. */
export function researchDrawingSymbol(identity: string) {
  let hash = 14695981039346656037n;
  for (const c of identity) hash = BigInt.asUintN(64, (hash ^ BigInt(c.charCodeAt(0))) * 1099511628211n);
  return `^R${hash.toString(36).toUpperCase()}`;
}
