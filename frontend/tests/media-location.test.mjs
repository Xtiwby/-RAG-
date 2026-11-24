import test from 'node:test'
import assert from 'node:assert/strict'
import { isMediaDocument, mediaStartSeconds } from '../src/media-location.mjs'

test('transcript citation ranges locate hours, minutes and seconds', () => {
  assert.equal(mediaStartSeconds('01:02:03 – 01:02:10'), 3723)
  assert.equal(mediaStartSeconds('00:00:00 – 00:00:05'), 0)
})

test('sampled video frame citations locate the frame time', () => {
  assert.equal(mediaStartSeconds('视频画面 00:02:30'), 150)
})

test('fractional timestamps retain their precision', () => {
  assert.equal(mediaStartSeconds('00:01:02.5 – 00:01:04'), 62.5)
})

test('missing or invalid timestamps are not presented as zero-second citations', () => {
  for (const value of [undefined, '', '第一章', '00:60:00', '00:00:99', '00:00:01not-time']) {
    assert.equal(mediaStartSeconds(value), null)
  }
})

test('media preview selection follows the original file type', () => {
  assert.equal(isMediaDocument({file_type: 'mp4'}), true)
  assert.equal(isMediaDocument({file_type: 'MP3'}), true)
  assert.equal(isMediaDocument({file_type: 'pdf', modality: 'video'}), false)
  assert.equal(isMediaDocument(null), false)
})
