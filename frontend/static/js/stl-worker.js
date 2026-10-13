// stl-worker.js — 在背景執行緒解析二進位 STL 檔案。
//
// 為什麼需要這個：STLLoader().parse() 是同步運算，對一個幾百萬面的
// STL（實際看過 7,432,374 個面的案例）解析要花好幾秒 CPU 時間，
// 如果在主執行緒做，瀏覽器在這幾秒內完全無法回應任何操作（包括切換分頁），
// 使用者會覺得「卡住」。丟到 Web Worker 背景執行緒解析，主執行緒
// 全程保持能回應，才能讓使用者「先切過去，看到載入中的畫面」。
//
// 這裡自己實作二進位 STL 的解析（格式很單純），不依賴 three.js
// （worker 環境沒有載入 three.js，也不需要 —— 只需要把原始位元組
// 轉成 Float32Array，真正建立 THREE.BufferGeometry 的工作留給主執行緒，
// 那個步驟本身很快，不是耗時的部分）。
//
// 二進位 STL 格式：
//   80 bytes  : header（忽略）
//   4 bytes   : uint32 三角形數量（little-endian）
//   每個三角形 50 bytes：
//     12 bytes : 法向量（3 個 float32）
//     36 bytes : 3 個頂點座標（各 3 個 float32）
//     2 bytes  : attribute byte count（忽略）

self.onmessage = function (e) {
  const { id, buffer } = e.data;

  try {
    const view = new DataView(buffer);

    if (buffer.byteLength < 84) {
      throw new Error('檔案太小，不是合法的二進位 STL');
    }

    const triangleCount = view.getUint32(80, true);
    const expectedSize = 84 + triangleCount * 50;

    if (expectedSize !== buffer.byteLength) {
      // 檔案大小跟「header + 三角形數量 * 50 bytes」對不起來，
      // 很可能是 ASCII STL 或損毀的檔案，這裡不處理 ASCII 格式
      // （STAR-CCM+ 輸出的模型檔案一律是二進位格式，數百萬面的
      // 模型如果是 ASCII 文字格式會大到不合理，實務上不會遇到）。
      throw new Error(
        `檔案大小與二進位 STL 格式不符（可能是 ASCII STL，目前不支援）：` +
        `預期 ${expectedSize} bytes，實際 ${buffer.byteLength} bytes`
      );
    }

    const positions = new Float32Array(triangleCount * 9);
    const normals = new Float32Array(triangleCount * 9);

    let offset = 84;
    for (let t = 0; t < triangleCount; t++) {
      const nx = view.getFloat32(offset, true);
      const ny = view.getFloat32(offset + 4, true);
      const nz = view.getFloat32(offset + 8, true);
      offset += 12;

      const base = t * 9;
      for (let v = 0; v < 3; v++) {
        positions[base + v * 3] = view.getFloat32(offset, true);
        positions[base + v * 3 + 1] = view.getFloat32(offset + 4, true);
        positions[base + v * 3 + 2] = view.getFloat32(offset + 8, true);
        // STL 每個面只有一個法向量，三個頂點都套用同一個
        // （非共享頂點的 flat shading，跟 three.js 自己的 STLLoader 行為一致）
        normals[base + v * 3] = nx;
        normals[base + v * 3 + 1] = ny;
        normals[base + v * 3 + 2] = nz;
        offset += 12;
      }
      offset += 2; // attribute byte count，跳過
    }

    self.postMessage(
      { id, positions, normals, triangleCount },
      [positions.buffer, normals.buffer]
    );
  } catch (err) {
    self.postMessage({ id, error: err.message });
  }
};
