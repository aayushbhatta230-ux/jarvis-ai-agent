/**
 * Standalone Zero-Dependency QR Code Generator
 * Generates ISO/IEC 18004 QR Codes directly to an HTML5 Canvas.
 */
(function(global) {
  'use strict';

  // Galois Field GF(256) math
  var EXP = new Uint8Array(512);
  var LOG = new Uint8Array(256);
  for (var i = 0, x = 1; i < 255; i++) {
    EXP[i] = x;
    EXP[i + 255] = x;
    LOG[x] = i;
    x = (x << 1) ^ (x >= 128 ? 0x11d : 0);
  }

  function gfMul(x, y) {
    return (x === 0 || y === 0) ? 0 : EXP[LOG[x] + LOG[y]];
  }

  function polyMul(p1, p2) {
    var res = new Uint8Array(p1.length + p2.length - 1);
    for (var i = 0; i < p1.length; i++) {
      for (var j = 0; j < p2.length; j++) {
        res[i + j] ^= gfMul(p1[i], p2[j]);
      }
    }
    return res;
  }

  function polyMod(dividend, divisor) {
    var result = new Uint8Array(dividend);
    for (var i = 0; i <= dividend.length - divisor.length; i++) {
      var coef = result[i];
      if (coef !== 0) {
        for (var j = 1; j < divisor.length; j++) {
          result[i + j] ^= gfMul(divisor[j], coef);
        }
      }
    }
    return result.slice(dividend.length - divisor.length + 1);
  }

  function getGenerator(ecCount) {
    var poly = new Uint8Array([1]);
    for (var i = 0; i < ecCount; i++) {
      poly = polyMul(poly, new Uint8Array([1, EXP[i]]));
    }
    return poly;
  }

  // Version 2 (25x25) - Byte Mode, ECC Level L (supports up to 32 bytes)
  // Version 3 (29x29) - Byte Mode, ECC Level L (supports up to 53 bytes)
  // Version 4 (33x33) - Byte Mode, ECC Level L (supports up to 78 bytes)
  var VERSIONS = [
    null,
    { ver: 1, size: 21, dataBytes: 19, ecBytes: 7, totalWords: 26, align: [] },
    { ver: 2, size: 25, dataBytes: 34, ecBytes: 10, totalWords: 44, align: [6, 18] },
    { ver: 3, size: 29, dataBytes: 55, ecBytes: 15, totalWords: 70, align: [6, 22] },
    { ver: 4, size: 33, dataBytes: 80, ecBytes: 20, totalWords: 100, align: [6, 26] }
  ];

  function encodeData(text, versionInfo) {
    var raw = [];
    for (var i = 0; i < text.length; i++) {
      var code = text.charCodeAt(i);
      if (code < 128) raw.push(code);
      else if (code < 2048) { raw.push(192 | (code >> 6), 128 | (code & 63)); }
      else { raw.push(224 | (code >> 12), 128 | ((code >> 6) & 63), 128 | (code & 63)); }
    }

    var bitStream = [];
    function putBits(val, len) {
      for (var b = len - 1; b >= 0; b--) {
        bitStream.push((val >> b) & 1);
      }
    }

    // Mode: Byte (0100)
    putBits(4, 4);
    // Char count (8 bits for versions 1-9)
    putBits(raw.length, 8);
    // Data bytes
    for (var j = 0; j < raw.length; j++) {
      putBits(raw[j], 8);
    }

    // Terminator (up to 4 zeroes)
    var maxBits = versionInfo.dataBytes * 8;
    var termLen = Math.min(4, maxBits - bitStream.length);
    for (var t = 0; t < termLen; t++) bitStream.push(0);

    // Byte align
    while (bitStream.length % 8 !== 0) bitStream.push(0);

    // Convert to bytes
    var bytes = [];
    for (var k = 0; k < bitStream.length; k += 8) {
      var byteVal = 0;
      for (var bit = 0; bit < 8; bit++) byteVal = (byteVal << 1) | bitStream[k + bit];
      bytes.push(byteVal);
    }

    // Pad bytes
    var padBytes = [0xEC, 0x11];
    var padIdx = 0;
    while (bytes.length < versionInfo.dataBytes) {
      bytes.push(padBytes[padIdx]);
      padIdx = (padIdx + 1) % 2;
    }

    // Error correction computation
    var genPoly = getGenerator(versionInfo.ecBytes);
    var toMod = new Uint8Array(versionInfo.dataBytes + versionInfo.ecBytes);
    for (var m = 0; m < bytes.length; m++) toMod[m] = bytes[m];
    var ecData = polyMod(toMod, genPoly);

    var finalBytes = bytes.slice();
    for (var e = 0; e < ecData.length; e++) finalBytes.push(ecData[e]);
    return finalBytes;
  }

  function createMatrix(versionInfo, bytes) {
    var size = versionInfo.size;
    var matrix = [];
    var isReserved = [];
    for (var r = 0; r < size; r++) {
      matrix.push(new Uint8Array(size));
      isReserved.push(new Uint8Array(size));
    }

    function setModule(row, col, val) {
      matrix[row][col] = val ? 1 : 0;
      isReserved[row][col] = 1;
    }

    // Finder patterns
    function addFinder(top, left) {
      for (var y = -1; y <= 7; y++) {
        for (var x = -1; x <= 7; x++) {
          var r = top + y, c = left + x;
          if (r < 0 || r >= size || c < 0 || c >= size) continue;
          if ((y >= 0 && y <= 6 && (x === 0 || x === 6)) ||
              (x >= 0 && x <= 6 && (y === 0 || y === 6)) ||
              (y >= 2 && y <= 4 && x >= 2 && x <= 4)) {
            setModule(r, c, 1);
          } else {
            setModule(r, c, 0);
          }
        }
      }
    }

    addFinder(0, 0);
    addFinder(0, size - 7);
    addFinder(size - 7, 0);

    // Alignment patterns
    if (versionInfo.align.length > 0) {
      var pos = versionInfo.align;
      for (var ai = 0; ai < pos.length; ai++) {
        for (var aj = 0; aj < pos.length; aj++) {
          var ar = pos[ai], ac = pos[aj];
          if (isReserved[ar][ac]) continue;
          for (var dy = -2; dy <= 2; dy++) {
            for (var dx = -2; dx <= 2; dx++) {
              var on = (Math.abs(dy) === 2 || Math.abs(dx) === 2 || (dy === 0 && dx === 0));
              setModule(ar + dy, ac + dx, on ? 1 : 0);
            }
          }
        }
      }
    }

    // Timing patterns
    for (var i = 8; i < size - 8; i++) {
      if (!isReserved[6][i]) setModule(6, i, i % 2 === 0);
      if (!isReserved[i][6]) setModule(i, 6, i % 2 === 0);
    }

    // Dark module
    setModule(size - 8, 8, 1);

    // Reserve format information areas
    for (var f = 0; f < 9; f++) {
      if (!isReserved[8][f]) isReserved[8][f] = 1;
      if (!isReserved[f][8]) isReserved[f][8] = 1;
      if (size - 1 - f >= 0) {
        if (!isReserved[8][size - 1 - f]) isReserved[8][size - 1 - f] = 1;
        if (!isReserved[size - 1 - f][8]) isReserved[size - 1 - f][8] = 1;
      }
    }

    // Place data bits using zigzag path
    var bitIdx = 0;
    var totalBits = bytes.length * 8;
    var right = size - 1;
    var dir = -1; // up
    while (right > 0) {
      if (right === 6) right--; // skip timing col
      for (var y = 0; y < size; y++) {
        var row = (dir === -1) ? (size - 1 - y) : y;
        for (var col = right; col >= right - 1; col--) {
          if (!isReserved[row][col]) {
            var bit = 0;
            if (bitIdx < totalBits) {
              var byteVal = bytes[Math.floor(bitIdx / 8)];
              bit = (byteVal >> (7 - (bitIdx % 8))) & 1;
              bitIdx++;
            }
            // Apply Mask 0: (row + col) % 2 === 0
            if ((row + col) % 2 === 0) bit ^= 1;
            matrix[row][col] = bit;
          }
        }
      }
      right -= 2;
      dir = -dir;
    }

    // Format bits for ECC Level L, Mask 0: 0x77C4 (15 bits)
    // 0111011111000100 in 15 bits = 111011111000100
    var formatBits = 0x77c4;
    for (var bi = 0; bi < 15; bi++) {
      var fbit = (formatBits >> bi) & 1;
      if (bi < 6) matrix[8][bi] = fbit;
      else if (bi < 8) matrix[8][bi + 1] = fbit;
      else matrix[8 - (bi - 8)][8] = fbit;

      if (bi < 8) matrix[size - 1 - bi][8] = fbit;
      else matrix[8][size - 15 + bi] = fbit;
    }

    return matrix;
  }

  function renderQR(canvas, text) {
    if (!canvas) return;
    var len = text.length;
    var ver = 2;
    if (len > 32) ver = 3;
    if (len > 53) ver = 4;
    var verInfo = VERSIONS[ver];
    var bytes = encodeData(text, verInfo);
    var matrix = createMatrix(verInfo, bytes);

    var ctx = canvas.getContext('2d');
    var size = verInfo.size;
    var canvasSize = canvas.width;
    var scale = canvasSize / (size + 4);
    var offset = scale * 2;

    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvasSize, canvasSize);

    ctx.fillStyle = '#06040f';
    for (var r = 0; r < size; r++) {
      for (var c = 0; c < size; c++) {
        if (matrix[r][c]) {
          ctx.fillRect(
            Math.round(offset + c * scale),
            Math.round(offset + r * scale),
            Math.ceil(scale),
            Math.ceil(scale)
          );
        }
      }
    }
  }

  global.drawQRCode = renderQR;
})(window);
