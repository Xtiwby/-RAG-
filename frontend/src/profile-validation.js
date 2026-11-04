// Match the existing account API limits; never trim or rewrite passwords.
export function normalizeDisplayName(value) {
  const name = String(value ?? '').trim()
  return name.length >= 1 && name.length <= 50 ? name : ''
}

export function canUpdatePassword(current, password, confirmation) {
  return typeof current === 'string' && current.length > 0 &&
    typeof password === 'string' && password.length >= 8 && password.length <= 128 &&
    password === confirmation
}
