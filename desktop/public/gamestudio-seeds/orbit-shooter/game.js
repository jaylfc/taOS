// Orbit Shooter - a small 3D arcade shooter built on three.js
// A turret sits at the center. Enemies drift in from a surrounding sphere.
// Aim with the mouse, fire with click or Space, don't let 3 enemies reach you.
import * as THREE from "./three.module.js";

// ---------- Renderer / scene / camera ----------

const container = document.getElementById("scene");
const scoreEl = document.getElementById("score");
const livesEl = document.getElementById("lives");
const overlayEl = document.getElementById("overlay");
const restartBtn = document.getElementById("restartBtn");

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05060a);
scene.fog = new THREE.Fog(0x05060a, 15, 45);

const camera = new THREE.PerspectiveCamera(65, window.innerWidth / window.innerHeight, 0.1, 200);
camera.position.set(0, 1.8, 4.5);
camera.lookAt(0, 0.5, -5);

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

scene.add(new THREE.AmbientLight(0x445577, 0.7));
const keyLight = new THREE.DirectionalLight(0xffffff, 1);
keyLight.position.set(3, 6, 4);
scene.add(keyLight);
const rimLight = new THREE.PointLight(0x66aaff, 1.2, 30);
rimLight.position.set(0, 2, -3);
scene.add(rimLight);

// ---------- Turret (the player) ----------

const turretGroup = new THREE.Object3D();
scene.add(turretGroup);

const turretBase = new THREE.Mesh(
  new THREE.CylinderGeometry(0.5, 0.6, 0.4, 16),
  new THREE.MeshStandardMaterial({ color: 0x33445a, roughness: 0.6 })
);
turretBase.position.y = 0.2;
turretGroup.add(turretBase);

const turretBarrel = new THREE.Mesh(
  new THREE.ConeGeometry(0.18, 1.1, 12),
  new THREE.MeshStandardMaterial({ color: 0xff5533, roughness: 0.4 })
);
turretBarrel.rotation.x = -Math.PI / 2; // point along -Z (forward)
turretBarrel.position.set(0, 0.45, -0.5);
turretGroup.add(turretBarrel);

// Aim limits keep the turret feeling like a turret rather than a free camera.
const MAX_YAW = Math.PI / 2.4;
const MAX_PITCH = Math.PI / 6;
let aimYaw = 0;
let aimPitch = 0;

window.addEventListener("mousemove", (e) => {
  const nx = e.clientX / window.innerWidth - 0.5; // -0.5..0.5
  const ny = e.clientY / window.innerHeight - 0.5;
  aimYaw = -nx * 2 * MAX_YAW;
  aimPitch = -ny * 2 * MAX_PITCH;
});

// ---------- Enemies ----------

const SPAWN_RADIUS = 18;
const enemies = [];
const enemyGeometry = new THREE.TetrahedronGeometry(0.5);
const enemyMaterial = new THREE.MeshStandardMaterial({ color: 0x66ff88, roughness: 0.4 });

function spawnEnemy() {
  // Spawn within the turret's forward-facing cone so it is reachable in play.
  const yaw = (Math.random() * 2 - 1) * MAX_YAW * 1.1;
  const pitch = (Math.random() * 2 - 1) * MAX_PITCH * 1.1;
  const dir = new THREE.Vector3(0, 0, -1)
    .applyAxisAngle(new THREE.Vector3(1, 0, 0), pitch)
    .applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
  const spawnPos = dir.multiplyScalar(SPAWN_RADIUS).add(new THREE.Vector3(0, 0.5, 0));

  const mesh = new THREE.Mesh(enemyGeometry, enemyMaterial);
  mesh.position.copy(spawnPos);
  scene.add(mesh);

  enemies.push({
    mesh,
    start: spawnPos.clone(),
    travelTime: 4 + Math.random() * 3, // seconds to reach the center
    elapsed: 0,
  });
}

let spawnTimer = 0;
const SPAWN_INTERVAL = 1.4;

// ---------- Projectiles ----------

const projectiles = [];
const projectileGeometry = new THREE.SphereGeometry(0.12, 8, 8);
const projectileMaterial = new THREE.MeshStandardMaterial({ color: 0xffee55, emissive: 0x664400 });
const PROJECTILE_SPEED = 22;

function fireProjectile() {
  const dir = new THREE.Vector3(0, 0, -1)
    .applyAxisAngle(new THREE.Vector3(1, 0, 0), aimPitch)
    .applyAxisAngle(new THREE.Vector3(0, 1, 0), aimYaw)
    .normalize();
  const mesh = new THREE.Mesh(projectileGeometry, projectileMaterial);
  mesh.position.set(0, 0.6, -0.5);
  scene.add(mesh);
  projectiles.push({ mesh, dir, life: 2 });
}

window.addEventListener("mousedown", fireProjectile);
window.addEventListener("keydown", (e) => {
  if (e.code === "Space") fireProjectile();
});

// ---------- Game state ----------

let score = 0;
let lives = 3;
let gameOver = false;

function updateHud() {
  scoreEl.textContent = `Score: ${score}`;
  livesEl.textContent = `Lives: ${lives}`;
}

function endGame() {
  gameOver = true;
  overlayEl.style.display = "flex";
}

function resetGame() {
  for (const enemy of enemies) scene.remove(enemy.mesh);
  enemies.length = 0;
  for (const proj of projectiles) scene.remove(proj.mesh);
  projectiles.length = 0;
  score = 0;
  lives = 3;
  spawnTimer = 0;
  gameOver = false;
  overlayEl.style.display = "none";
  updateHud();
}

restartBtn.addEventListener("click", resetGame);
updateHud();

// ---------- Update loop ----------

const clock = new THREE.Clock();
const HIT_DISTANCE = 0.7;
const REACH_DISTANCE = 1.2;

function updateTurret() {
  turretGroup.rotation.y = aimYaw;
  turretGroup.rotation.x = aimPitch;
}

function updateEnemies(dt) {
  for (let i = enemies.length - 1; i >= 0; i--) {
    const enemy = enemies[i];
    enemy.elapsed += dt;
    const t = Math.min(enemy.elapsed / enemy.travelTime, 1);
    enemy.mesh.position.lerpVectors(enemy.start, new THREE.Vector3(0, 0.6, 0), t);
    enemy.mesh.rotation.x += dt * 2;
    enemy.mesh.rotation.y += dt * 1.5;

    if (enemy.mesh.position.length() < REACH_DISTANCE) {
      scene.remove(enemy.mesh);
      enemies.splice(i, 1);
      lives -= 1;
      updateHud();
      if (lives <= 0) endGame();
    }
  }
}

function updateProjectiles(dt) {
  for (let i = projectiles.length - 1; i >= 0; i--) {
    const proj = projectiles[i];
    proj.mesh.position.addScaledVector(proj.dir, PROJECTILE_SPEED * dt);
    proj.life -= dt;

    let hit = false;
    for (let j = enemies.length - 1; j >= 0; j--) {
      if (proj.mesh.position.distanceTo(enemies[j].mesh.position) < HIT_DISTANCE) {
        scene.remove(enemies[j].mesh);
        enemies.splice(j, 1);
        score += 1;
        updateHud();
        hit = true;
        break;
      }
    }

    if (hit || proj.life <= 0) {
      scene.remove(proj.mesh);
      projectiles.splice(i, 1);
    }
  }
}

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);

  if (!gameOver) {
    spawnTimer -= dt;
    if (spawnTimer <= 0) {
      spawnEnemy();
      spawnTimer = SPAWN_INTERVAL;
    }
    updateTurret();
    updateEnemies(dt);
    updateProjectiles(dt);
  }

  renderer.render(scene, camera);
}

animate();
