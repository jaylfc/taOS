// Platformer Lite - a small 3D platformer built on three.js
// Jump across floating platforms, collect every gem, don't fall off.
import * as THREE from "./three.module.js";

// ---------- Renderer / scene / camera ----------

const container = document.getElementById("scene");
const scoreEl = document.getElementById("score");
const messageEl = document.getElementById("message");

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x8fd0f0);
scene.fog = new THREE.Fog(0x8fd0f0, 30, 90);

const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 200);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
container.appendChild(renderer.domElement);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// ---------- Lighting ----------

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const sun = new THREE.DirectionalLight(0xfff2d6, 1.1);
sun.position.set(8, 14, 6);
scene.add(sun);
const fillLight = new THREE.DirectionalLight(0x99ccff, 0.4);
fillLight.position.set(-6, 5, -8);
scene.add(fillLight);

// A big soft-colored sphere behind everything gives a cheap "sky" without images.
const sky = new THREE.Mesh(
  new THREE.SphereGeometry(150, 16, 16),
  new THREE.MeshBasicMaterial({ color: 0xbfe6ff, side: THREE.BackSide })
);
scene.add(sky);

// ---------- Platforms ----------
// Each platform is a box; we track its top-surface bounds for collision.
const platformDefs = [
  { x: 0, y: 0, z: 0, w: 6, h: 1, d: 6, color: 0x6bbf6b },
  { x: 6, y: 1, z: -4, w: 4, h: 1, d: 4, color: 0x5aa9d6 },
  { x: 11, y: 2, z: -2, w: 3.5, h: 1, d: 3.5, color: 0xd6a15a },
  { x: 14, y: 3, z: 3, w: 3.5, h: 1, d: 3.5, color: 0xd65a8c },
  { x: 10, y: 4, z: 8, w: 4, h: 1, d: 4, color: 0x9a5ad6 },
  { x: 3, y: 3, z: 8, w: 4, h: 1, d: 4, color: 0x5ad6c0 },
];

const platforms = [];
for (const def of platformDefs) {
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(def.w, def.h, def.d),
    new THREE.MeshStandardMaterial({ color: def.color, roughness: 0.8 })
  );
  mesh.position.set(def.x, def.y, def.z);
  scene.add(mesh);
  platforms.push({
    top: def.y + def.h / 2,
    minX: def.x - def.w / 2,
    maxX: def.x + def.w / 2,
    minZ: def.z - def.d / 2,
    maxZ: def.z + def.d / 2,
  });
}

// ---------- Player ----------

const START_POS = new THREE.Vector3(0, 1.6, 0);
const PLAYER_RADIUS = 0.4;
const PLAYER_HALF_HEIGHT = 0.6;

const player = new THREE.Mesh(
  new THREE.CapsuleGeometry(PLAYER_RADIUS, 0.6, 4, 8),
  new THREE.MeshStandardMaterial({ color: 0xff5533, roughness: 0.5 })
);
player.position.copy(START_POS);
scene.add(player);

const velocity = new THREE.Vector3(0, 0, 0);
let onGround = false;

// ---------- Collectibles ----------

const collectibleSpots = [
  { x: 0, y: 1.3, z: 0 },
  { x: 6, y: 2.3, z: -4 },
  { x: 11, y: 3.3, z: -2 },
  { x: 14, y: 4.3, z: 3 },
  { x: 10, y: 5.3, z: 8 },
  { x: 3, y: 4.3, z: 8 },
];

const gemGeometry = new THREE.SphereGeometry(0.3, 12, 12);
const gemMaterial = new THREE.MeshStandardMaterial({ color: 0xffd84a, emissive: 0x664400, roughness: 0.3 });
const gems = collectibleSpots.map((spot) => {
  const gem = new THREE.Mesh(gemGeometry, gemMaterial);
  gem.position.set(spot.x, spot.y, spot.z);
  scene.add(gem);
  return gem;
});

const totalGems = gems.length;
let score = 0;
scoreEl.textContent = `Gems: ${score} / ${totalGems}`;

// ---------- Input ----------

const keys = new Set();
window.addEventListener("keydown", (e) => keys.add(e.code));
window.addEventListener("keyup", (e) => keys.delete(e.code));

const MOVE_SPEED = 6;
const JUMP_SPEED = 8;
const GRAVITY = 20;
const FALL_RESPAWN_Y = -15;

// ---------- Camera follow state ----------

const cameraOffset = new THREE.Vector3(0, 4, 7);
const cameraTarget = new THREE.Vector3();

// ---------- Game loop ----------

const clock = new THREE.Clock();

function resetPlayer() {
  player.position.copy(START_POS);
  velocity.set(0, 0, 0);
}

function updatePlayer(dt) {
  // Horizontal input (world-axis movement, kept simple for readability).
  let moveX = 0;
  let moveZ = 0;
  if (keys.has("KeyW") || keys.has("ArrowUp")) moveZ -= 1;
  if (keys.has("KeyS") || keys.has("ArrowDown")) moveZ += 1;
  if (keys.has("KeyA") || keys.has("ArrowLeft")) moveX -= 1;
  if (keys.has("KeyD") || keys.has("ArrowRight")) moveX += 1;

  const moveLength = Math.hypot(moveX, moveZ);
  if (moveLength > 0) {
    moveX = (moveX / moveLength) * MOVE_SPEED;
    moveZ = (moveZ / moveLength) * MOVE_SPEED;
  }

  if ((keys.has("Space")) && onGround) {
    velocity.y = JUMP_SPEED;
    onGround = false;
  }

  velocity.y -= GRAVITY * dt;

  player.position.x += moveX * dt;
  player.position.z += moveZ * dt;
  player.position.y += velocity.y * dt;

  // Simple downward collision check: land on a platform if the player's
  // feet cross its top while inside its horizontal bounds.
  onGround = false;
  const feetY = player.position.y - PLAYER_HALF_HEIGHT;
  for (const p of platforms) {
    const insideX = player.position.x > p.minX - PLAYER_RADIUS && player.position.x < p.maxX + PLAYER_RADIUS;
    const insideZ = player.position.z > p.minZ - PLAYER_RADIUS && player.position.z < p.maxZ + PLAYER_RADIUS;
    if (insideX && insideZ && velocity.y <= 0 && feetY <= p.top && feetY > p.top - 0.5) {
      player.position.y = p.top + PLAYER_HALF_HEIGHT;
      velocity.y = 0;
      onGround = true;
    }
  }

  if (player.position.y < FALL_RESPAWN_Y) {
    resetPlayer();
  }
}

function updateCollectibles() {
  for (let i = gems.length - 1; i >= 0; i--) {
    const gem = gems[i];
    if (gem.position.distanceTo(player.position) < 0.9) {
      scene.remove(gem);
      gems.splice(i, 1);
      score += 1;
      scoreEl.textContent = `Gems: ${score} / ${totalGems}`;
      if (score === totalGems) {
        messageEl.textContent = "You collected them all!";
        messageEl.style.display = "block";
      }
    } else {
      // Gentle bob + spin so gems read as collectible.
      gem.rotation.y += 0.03;
      gem.position.y += Math.sin(performance.now() / 400 + gem.id) * 0.0015;
    }
  }
}

function updateCamera(dt) {
  cameraTarget.copy(player.position).add(cameraOffset);
  camera.position.lerp(cameraTarget, 1 - Math.pow(0.001, dt));
  const lookTarget = player.position.clone().add(new THREE.Vector3(0, 0.5, 0));
  camera.lookAt(lookTarget);
}

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);
  updatePlayer(dt);
  updateCollectibles();
  updateCamera(dt);
  renderer.render(scene, camera);
}

animate();
