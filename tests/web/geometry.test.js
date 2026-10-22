import test from 'node:test';
import assert from 'node:assert/strict';
import {
  area, intersect, iou, normRect, makeView, toScreen, toPage, viewSize, toUserSpace,
} from '../../demo/web/core/geometry.js';

const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test('identity view maps points to pixels', () => {
  const v = makeView({ width: 612, height: 792, rotation: 0, scale: 2 });
  assert.deepEqual(toScreen([72, 100, 172, 120], v), { left: 144, top: 200, width: 200, height: 40 });
});

for (const rotation of [0, 90, 180, 270]) {
  test(`round trip at rotation ${rotation}`, () => {
    const v = makeView({ width: 612, height: 792, rotation, scale: 1.5 });
    const r = [50.5, 60, 300, 90.25];
    const back = toPage(toScreen(r, v), v);
    r.forEach((x, i) => assert.ok(Math.abs(x - back[i]) < 1e-6));
  });
}

test('rotation 90 puts the page top-left corner at screen top-right', () => {
  const v = makeView({ width: 612, height: 792, rotation: 90, scale: 1 });
  const s = toScreen([0, 0, 10, 10], v);
  assert.deepEqual(viewSize(v), { width: 792, height: 612 });
  assert.equal(s.left, 792 - 10);
  assert.equal(s.top, 0);
});

test('each rotation puts the page top-left corner where a clockwise turn would', () => {
  // The 10x10 box at the page's top-left corner lands in the matching corner of the screen.
  const corner = (rotation) => {
    const v = makeView({ width: 612, height: 792, rotation, scale: 1 });
    const { width, height } = viewSize(v);
    const s = toScreen([0, 0, 10, 10], v);
    assert.equal(s.width, 10);
    assert.equal(s.height, 10);
    const horizontal = s.left === 0 ? 'left' : (s.left + s.width === width ? 'right' : 'middle');
    const vertical = s.top === 0 ? 'top' : (s.top + s.height === height ? 'bottom' : 'middle');
    return [horizontal, vertical];
  };
  assert.deepEqual(corner(0), ['left', 'top']);
  assert.deepEqual(corner(90), ['right', 'top']);
  assert.deepEqual(corner(180), ['right', 'bottom']);
  assert.deepEqual(corner(270), ['left', 'bottom']);
});

test('rotation 180 and 270 map a known rect exactly', () => {
  const v180 = makeView({ width: 612, height: 792, rotation: 180, scale: 1 });
  assert.deepEqual(toScreen([100, 200, 150, 220], v180), { left: 462, top: 572, width: 50, height: 20 });
  const v270 = makeView({ width: 612, height: 792, rotation: 270, scale: 1 });
  assert.deepEqual(toScreen([100, 200, 150, 220], v270), { left: 200, top: 462, width: 20, height: 50 });
});

test('rotation accepts negatives and full turns, rejects other angles', () => {
  assert.equal(makeView({ width: 1, height: 1, rotation: -90 }).rotation, 270);
  assert.equal(makeView({ width: 1, height: 1, rotation: 450 }).rotation, 90);
  assert.throws(() => makeView({ width: 1, height: 1, rotation: 45 }), RangeError);
});

test('viewSize scales and swaps', () => {
  assert.deepEqual(viewSize(makeView({ width: 612, height: 792, rotation: 0, scale: 2 })), { width: 1224, height: 1584 });
  assert.deepEqual(viewSize(makeView({ width: 612, height: 792, rotation: 270, scale: 2 })), { width: 1584, height: 1224 });
});

test('user space honours the crop box offset', () => {
  assert.deepEqual(toUserSpace([10, 20, 110, 40], [36, 36, 648, 828]), [46, 788, 146, 808]);
});

test('user space is independent of rotation and a crop box at the origin flips y only', () => {
  assert.deepEqual(toUserSpace([0, 0, 612, 792], [0, 0, 612, 792]), [0, 0, 612, 792]);
  assert.deepEqual(toUserSpace([72, 100, 172, 120], [0, 0, 612, 792]), [72, 672, 172, 692]);
});

test('screen placement of a box does not depend on the crop box offset', () => {
  // Rects are crop-box relative; only toUserSpace sees the crop box.
  const v = makeView({ width: 612, height: 792, rotation: 0, scale: 1 });
  assert.deepEqual(toScreen([10, 20, 110, 40], v), { left: 10, top: 20, width: 100, height: 20 });
});

test('iou', () => {
  assert.equal(iou([0, 0, 10, 10], [0, 0, 10, 10]), 1);
  assert.equal(iou([0, 0, 10, 10], [20, 20, 30, 30]), 0);
  close(iou([0, 0, 10, 10], [5, 0, 15, 10]), 50 / 150);
  assert.equal(iou([0, 0, 0, 10], [0, 0, 0, 10]), 0);
});

test('area, intersect and normRect', () => {
  assert.equal(area([0, 0, 4, 5]), 20);
  assert.equal(area([4, 5, 0, 0]), 0);
  assert.equal(intersect([0, 0, 10, 10], [10, 0, 20, 10]), null);
  assert.deepEqual(intersect([0, 0, 10, 10], [5, 5, 20, 20]), [5, 5, 10, 10]);
  assert.deepEqual(normRect([10, 8, 2, 3]), [2, 3, 10, 8]);
});
