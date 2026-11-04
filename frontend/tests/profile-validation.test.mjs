import test from 'node:test'
import assert from 'node:assert/strict'
import {normalizeDisplayName,canUpdatePassword} from '../src/profile-validation.js'

test('display names are trimmed and retain Chinese text',()=>{
  assert.equal(normalizeDisplayName('  示例成员  '),'示例成员')
})
test('blank display names are rejected',()=>{
  for(const value of ['', '   ', null, undefined]) assert.equal(normalizeDisplayName(value),'')
})
test('display names enforce the existing 1–50 length bounds',()=>{
  assert.equal(normalizeDisplayName('名'),'名')
  assert.equal(normalizeDisplayName('名'.repeat(50)).length,50)
  assert.equal(normalizeDisplayName('名'.repeat(51)),'')
})
test('password submission requires all fields and matching confirmation',()=>{
  assert.equal(canUpdatePassword('', 'example1', 'example1'),false)
  assert.equal(canUpdatePassword('current', '', ''),false)
  assert.equal(canUpdatePassword('current', 'example1', 'example2'),false)
  assert.equal(canUpdatePassword('current', 'example1', 'example1'),true)
})
test('password submission enforces the existing 8–128 length bounds',()=>{
  for(const length of [7,129]) assert.equal(canUpdatePassword('current','x'.repeat(length),'x'.repeat(length)),false)
  for(const length of [8,128]) assert.equal(canUpdatePassword('current','x'.repeat(length),'x'.repeat(length)),true)
})
test('password whitespace is preserved rather than trimmed',()=>{
  assert.equal(canUpdatePassword('current',' example ',' example '),true)
  assert.equal(canUpdatePassword('current',' example ','example'),false)
})
