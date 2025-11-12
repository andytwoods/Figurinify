// Figurinify — load GLTF, add adjustable base, set target height, export STL
// Uses Three.js from CDN (ES modules). For best results, open via a local server.

import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';
import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'https://unpkg.com/three@0.160.0/examples/jsm/loaders/GLTFLoader.js';
import { STLExporter } from 'https://unpkg.com/three@0.160.0/examples/jsm/exporters/STLExporter.js';

// DOM elements
const viewportEl = document.getElementById('viewport');
const heightInput = document.getElementById('heightInput');
const applyHeightBtn = document.getElementById('applyHeight');
const baseRadiusSlider = document.getElementById('baseRadius');
const baseRadiusValue = document.getElementById('baseRadiusValue');
const baseHeightInput = document.getElementById('baseHeight');
const fileInput = document.getElementById('fileInput');
const exportBtn = document.getElementById('exportSTL');
const resetViewBtn = document.getElementById('resetView');

// THREE.js essentials
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x111111);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
viewportEl.appendChild(renderer.domElement);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);
camera.position.set(120, 100, 160);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.05;
controls.target.set(0, 40, 0);

// Lights
const hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
hemi.position.set(0, 1, 0);
scene.add(hemi);

const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
dirLight.position.set(100, 200, 100);
dirLight.castShadow = true;
dirLight.shadow.mapSize.set(2048, 2048);
dirLight.shadow.camera.near = 10;
dirLight.shadow.camera.far = 1000;
dirLight.shadow.camera.left = -200;
dirLight.shadow.camera.right = 200;
dirLight.shadow.camera.top = 200;
dirLight.shadow.camera.bottom = -200;
scene.add(dirLight);

// Ground grid (for reference only, not exported)
const grid = new THREE.GridHelper(400, 40, 0x444444, 0x222222);
scene.add(grid);

// Groups: we'll export this combined group
const exportGroup = new THREE.Group();
scene.add(exportGroup);

const modelGroup = new THREE.Group();
modelGroup.name = 'ModelGroup';
exportGroup.add(modelGroup);

let baseMesh = null;

// Materials
const baseMaterial = new THREE.MeshStandardMaterial({ color: 0x777777, metalness: 0.0, roughness: 1.0 });

// GLTF Loader
const loader = new GLTFLoader();

function resizeRenderer() {
  const rect = viewportEl.getBoundingClientRect();
  const width = Math.max(200, rect.width);
  const height = Math.max(200, rect.height);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resizeRenderer);

function createOrUpdateBase(radiusMM, heightMM) {
  if (radiusMM <= 0) radiusMM = 1;
  if (heightMM <= 0) heightMM = 1;

  const segments = 64;
  const geometry = new THREE.CylinderGeometry(radiusMM, radiusMM, heightMM, segments);

  if (!baseMesh) {
    baseMesh = new THREE.Mesh(geometry, baseMaterial);
    baseMesh.receiveShadow = true;
    baseMesh.castShadow = false;
    exportGroup.add(baseMesh);
  } else {
    baseMesh.geometry.dispose();
    baseMesh.geometry = geometry;
  }
  // We keep the top face at y = 0 so the model sits on it.
  baseMesh.position.set(0, -heightMM / 2, 0);
}

function computeBBox(object3d) {
  const box = new THREE.Box3().setFromObject(object3d);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  return { box, size, center };
}

function alignModelOnBase() {
  // Move model so its lowest point sits at y = 0 (top of base)
  const { box } = computeBBox(modelGroup);
  if (!isFinite(box.min.y) || !isFinite(box.max.y)) return;
  const minY = box.min.y;
  modelGroup.position.y += -minY; // bring lowest point to y=0
}

function centerModelXZ() {
  const { box, center } = computeBBox(modelGroup);
  if (!isFinite(center.x) || !isFinite(center.z)) return;
  const offsetX = -center.x;
  const offsetZ = -center.z;
  modelGroup.position.x += offsetX;
  modelGroup.position.z += offsetZ;
}

function scaleModelToHeight(targetHeightMM) {
  // Compute current height of modelGroup (before alignment), then scale uniformly
  const { box } = computeBBox(modelGroup);
  const currentHeight = box.max.y - box.min.y;
  if (currentHeight <= 0) return;
  const s = targetHeightMM / currentHeight;
  modelGroup.scale.setScalar(s);
}

function fitCameraToObject(object, padding = 20) {
  const { box, size, center } = computeBBox(object);
  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = camera.fov * (Math.PI / 180);
  let cameraZ = Math.abs(maxDim / (2 * Math.tan(fov / 2)));
  cameraZ *= 1.2; // a bit further

  camera.position.set(center.x + cameraZ, center.y + cameraZ * 0.6, center.z + cameraZ);
  controls.target.copy(new THREE.Vector3(center.x, Math.max(0, center.y - size.y / 2), center.z));
  controls.update();
}

async function loadDefaultModel() {
  try {
    const gltf = await loader.loadAsync('model/scene.gltf');
    setModelFromGLTF(gltf);
  } catch (e) {
    console.warn('Could not load default model from model/scene.gltf. You can load a .glb via the file input.', e);
  }
}

function clearGroup(group) {
  for (let i = group.children.length - 1; i >= 0; i--) {
    const child = group.children[i];
    group.remove(child);
  }
}

function enableShadows(object, cast = true, receive = true) {
  object.traverse((obj) => {
    if (obj.isMesh) {
      obj.castShadow = cast;
      obj.receiveShadow = receive;
    }
  });
}

function setModelFromGLTF(gltf) {
  clearGroup(modelGroup);

  const root = gltf.scene || gltf.scenes?.[0];
  modelGroup.add(root);
  enableShadows(root, true, true);

  // Initial normalization: center XZ, bring feet to y=0, scale to target height, then realign
  centerModelXZ();
  alignModelOnBase();
  scaleModelToHeight(Number(heightInput.value));
  centerModelXZ();
  alignModelOnBase();

  fitCameraToObject(exportGroup);
}

function handleFile(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith('.glb')) {
    const url = URL.createObjectURL(file);
    loader.load(url, (gltf) => {
      setModelFromGLTF(gltf);
      URL.revokeObjectURL(url);
    }, undefined, (err) => {
      console.error('Failed to load GLB:', err);
      URL.revokeObjectURL(url);
    });
  } else if (name.endsWith('.gltf')) {
    // GLTF with external resources may not load correctly from file input due to relative paths.
    // Recommend using a .glb. We'll still try, but textures might fail.
    const url = URL.createObjectURL(file);
    loader.load(url, (gltf) => {
      setModelFromGLTF(gltf);
      URL.revokeObjectURL(url);
    }, undefined, (err) => {
      console.error('Failed to load GLTF:', err);
      URL.revokeObjectURL(url);
    });
  } else {
    alert('Unsupported file format. Please select a .glb or .gltf file.');
  }
}

function doExportSTL() {
  // Export the exportGroup (model + base)
  const exporter = new STLExporter();
  const binary = true;
  const result = exporter.parse(exportGroup, { binary });
  let blob;
  let filename = 'figure_with_base.stl';

  if (binary && result instanceof ArrayBuffer) {
    blob = new Blob([result], { type: 'application/vnd.ms-pki.stl' });
  } else if (typeof result === 'string') {
    blob = new Blob([result], { type: 'text/plain' });
  } else {
    console.error('Unexpected STL export result');
    return;
  }

  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

// UI handlers
baseRadiusSlider.addEventListener('input', () => {
  const r = Number(baseRadiusSlider.value);
  baseRadiusValue.textContent = String(r);
  createOrUpdateBase(r, Number(baseHeightInput.value));
  // Optionally keep camera focus at center
});

baseHeightInput.addEventListener('change', () => {
  createOrUpdateBase(Number(baseRadiusSlider.value), Number(baseHeightInput.value));
});

applyHeightBtn.addEventListener('click', () => {
  if (modelGroup.children.length === 0) return;
  scaleModelToHeight(Number(heightInput.value));
  alignModelOnBase();
  fitCameraToObject(exportGroup);
});

fileInput.addEventListener('change', (e) => {
  const file = e.target.files?.[0];
  if (file) handleFile(file);
});

resetViewBtn.addEventListener('click', () => {
  fitCameraToObject(exportGroup);
});

exportBtn.addEventListener('click', () => {
  doExportSTL();
});

// Drag and drop (optional convenience)
window.addEventListener('dragover', (e) => {
  e.preventDefault();
});
window.addEventListener('drop', (e) => {
  e.preventDefault();
  const file = e.dataTransfer?.files?.[0];
  if (file) handleFile(file);
});

// Animation loop
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// Initial setup
function init() {
  resizeRenderer();
  createOrUpdateBase(Number(baseRadiusSlider.value), Number(baseHeightInput.value));
  loadDefaultModel();
  animate();
}

init();
