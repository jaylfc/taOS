// Asteroid Miner - 2D wraparound asteroids-style shooter, plain canvas 2D, no libraries.
// Rotate and thrust a ship, blast drifting asteroids, wrap at every edge.
// Keyboard (rotate/thrust/fire) and on-screen touch buttons both drive the
// same input state, so touch devices get real controls without a second
// code path.

const container = document.getElementById("scene");
const scoreEl = document.getElementById("score");
const livesEl = document.getElementById("lives");
const overlayEl = document.getElementById("overlay");
const restartBtn = document.getElementById("restartBtn");

const canvas = document.createElement("canvas");
canvas.style.display = "block";
const ctx = canvas.getContext("2d");
container.appendChild(canvas);

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

// ---------- Constants ----------

const SHIP_RADIUS = 14;
const ROTATE_SPEED = 3.2; // rad/sec
const THRUST_ACCEL = 260; // px/sec^2
const DRAG = 0.55; // velocity multiplier per second (soft space friction)
const MAX_SPEED = 340;
const BULLET_SPEED = 460;
const BULLET_LIFE = 1.1; // seconds
const ASTEROID_RADIUS = 26;
const ASTEROID_SPEED_MIN = 40;
const ASTEROID_SPEED_MAX = 110;
const SPAWN_INTERVAL = 1.6;
const INVULN_TIME = 1.5;

// ---------- Input state ----------

const input = { left: false, right: false, thrust: false };

function bindHold(el, key) {
  const set = (v) => (input[key] = v);
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    set(true);
  });
  el.addEventListener("pointerup", () => set(false));
  el.addEventListener("pointerleave", () => set(false));
  el.addEventListener("pointercancel", () => set(false));
}
bindHold(document.getElementById("btn-left"), "left");
bindHold(document.getElementById("btn-right"), "right");
bindHold(document.getElementById("btn-thrust"), "thrust");
document.getElementById("btn-fire").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  fireBullet();
});

window.addEventListener("keydown", (e) => {
  if (e.code === "ArrowLeft" || e.code === "KeyA") input.left = true;
  if (e.code === "ArrowRight" || e.code === "KeyD") input.right = true;
  if (e.code === "ArrowUp" || e.code === "KeyW") input.thrust = true;
  if (e.code === "Space") fireBullet();
});
window.addEventListener("keyup", (e) => {
  if (e.code === "ArrowLeft" || e.code === "KeyA") input.left = false;
  if (e.code === "ArrowRight" || e.code === "KeyD") input.right = false;
  if (e.code === "ArrowUp" || e.code === "KeyW") input.thrust = false;
});
canvas.addEventListener("click", () => fireBullet());

// ---------- Game state ----------

let ship;
let bullets;
let asteroids;
let score;
let lives;
let gameOver;
let spawnTimer;

function resetGame() {
  ship = {
    x: canvas.width / 2,
    y: canvas.height / 2,
    angle: 0,
    vx: 0,
    vy: 0,
    invuln: INVULN_TIME,
  };
  bullets = [];
  asteroids = [];
  score = 0;
  lives = 3;
  gameOver = false;
  spawnTimer = 0;
  for (let i = 0; i < 4; i++) spawnAsteroid();
  updateHud();
  overlayEl.style.display = "none";
}

function updateHud() {
  scoreEl.textContent = `Score: ${score}`;
  livesEl.textContent = `Lives: ${lives}`;
}

function wrap(pos, max) {
  if (pos < 0) return pos + max;
  if (pos > max) return pos - max;
  return pos;
}

function spawnAsteroid() {
  // Spawn just outside a random edge, drifting inward-ish.
  const edge = Math.floor(Math.random() * 4);
  let x, y;
  if (edge === 0) { x = -ASTEROID_RADIUS; y = Math.random() * canvas.height; }
  else if (edge === 1) { x = canvas.width + ASTEROID_RADIUS; y = Math.random() * canvas.height; }
  else if (edge === 2) { x = Math.random() * canvas.width; y = -ASTEROID_RADIUS; }
  else { x = Math.random() * canvas.width; y = canvas.height + ASTEROID_RADIUS; }

  const speed = ASTEROID_SPEED_MIN + Math.random() * (ASTEROID_SPEED_MAX - ASTEROID_SPEED_MIN);
  const dir = Math.random() * Math.PI * 2;
  asteroids.push({
    x, y,
    vx: Math.cos(dir) * speed,
    vy: Math.sin(dir) * speed,
    radius: ASTEROID_RADIUS * (0.7 + Math.random() * 0.5),
    rotation: Math.random() * Math.PI * 2,
    spin: (Math.random() - 0.5) * 2,
  });
}

function fireBullet() {
  if (gameOver) {
    resetGame();
    return;
  }
  const noseX = ship.x + Math.sin(ship.angle) * SHIP_RADIUS;
  const noseY = ship.y - Math.cos(ship.angle) * SHIP_RADIUS;
  bullets.push({
    x: noseX,
    y: noseY,
    vx: Math.sin(ship.angle) * BULLET_SPEED,
    vy: -Math.cos(ship.angle) * BULLET_SPEED,
    life: BULLET_LIFE,
  });
}

restartBtn.addEventListener("click", resetGame);
resetGame();

// ---------- Update ----------

function updateShip(dt) {
  if (input.left) ship.angle -= ROTATE_SPEED * dt;
  if (input.right) ship.angle += ROTATE_SPEED * dt;

  if (input.thrust) {
    ship.vx += Math.sin(ship.angle) * THRUST_ACCEL * dt;
    ship.vy -= Math.cos(ship.angle) * THRUST_ACCEL * dt;
  }

  const dragFactor = Math.pow(DRAG, dt);
  ship.vx *= dragFactor;
  ship.vy *= dragFactor;

  const speed = Math.hypot(ship.vx, ship.vy);
  if (speed > MAX_SPEED) {
    ship.vx = (ship.vx / speed) * MAX_SPEED;
    ship.vy = (ship.vy / speed) * MAX_SPEED;
  }

  ship.x = wrap(ship.x + ship.vx * dt, canvas.width);
  ship.y = wrap(ship.y + ship.vy * dt, canvas.height);

  if (ship.invuln > 0) ship.invuln -= dt;
}

function updateBullets(dt) {
  for (let i = bullets.length - 1; i >= 0; i--) {
    const b = bullets[i];
    b.x += b.vx * dt;
    b.y += b.vy * dt;
    b.life -= dt;
    if (b.life <= 0 || b.x < 0 || b.x > canvas.width || b.y < 0 || b.y > canvas.height) {
      bullets.splice(i, 1);
    }
  }
}

function updateAsteroids(dt) {
  for (const a of asteroids) {
    a.x = wrap(a.x + a.vx * dt, canvas.width);
    a.y = wrap(a.y + a.vy * dt, canvas.height);
    a.rotation += a.spin * dt;
  }

  spawnTimer -= dt;
  if (spawnTimer <= 0) {
    spawnAsteroid();
    spawnTimer = SPAWN_INTERVAL;
  }
}

function checkCollisions() {
  // Bullet vs asteroid
  for (let i = asteroids.length - 1; i >= 0; i--) {
    const a = asteroids[i];
    for (let j = bullets.length - 1; j >= 0; j--) {
      const b = bullets[j];
      if (Math.hypot(a.x - b.x, a.y - b.y) < a.radius) {
        asteroids.splice(i, 1);
        bullets.splice(j, 1);
        score += 10;
        updateHud();
        break;
      }
    }
  }
  while (asteroids.length < 3) spawnAsteroid();

  // Ship vs asteroid
  if (ship.invuln <= 0) {
    for (const a of asteroids) {
      if (Math.hypot(a.x - ship.x, a.y - ship.y) < a.radius + SHIP_RADIUS * 0.7) {
        lives -= 1;
        updateHud();
        if (lives <= 0) {
          gameOver = true;
          overlayEl.style.display = "flex";
        } else {
          ship.x = canvas.width / 2;
          ship.y = canvas.height / 2;
          ship.vx = 0;
          ship.vy = 0;
          ship.invuln = INVULN_TIME;
        }
        break;
      }
    }
  }
}

// ---------- Draw ----------

function draw() {
  ctx.fillStyle = "#0a0a14";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Ship
  ctx.save();
  ctx.translate(ship.x, ship.y);
  ctx.rotate(ship.angle);
  ctx.globalAlpha = ship.invuln > 0 ? 0.5 + 0.4 * Math.sin(performance.now() / 80) : 1;
  ctx.fillStyle = "#8fd6ff";
  ctx.beginPath();
  ctx.moveTo(0, -SHIP_RADIUS);
  ctx.lineTo(SHIP_RADIUS * 0.75, SHIP_RADIUS * 0.85);
  ctx.lineTo(0, SHIP_RADIUS * 0.45);
  ctx.lineTo(-SHIP_RADIUS * 0.75, SHIP_RADIUS * 0.85);
  ctx.closePath();
  ctx.fill();
  if (input.thrust) {
    ctx.fillStyle = "#ffae42";
    ctx.beginPath();
    ctx.moveTo(-SHIP_RADIUS * 0.3, SHIP_RADIUS * 0.6);
    ctx.lineTo(0, SHIP_RADIUS * 1.3);
    ctx.lineTo(SHIP_RADIUS * 0.3, SHIP_RADIUS * 0.6);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
  ctx.globalAlpha = 1;

  // Bullets
  ctx.fillStyle = "#ffee55";
  for (const b of bullets) {
    ctx.beginPath();
    ctx.arc(b.x, b.y, 3, 0, Math.PI * 2);
    ctx.fill();
  }

  // Asteroids
  ctx.strokeStyle = "#c9a877";
  ctx.lineWidth = 2;
  ctx.fillStyle = "#7a6244";
  for (const a of asteroids) {
    ctx.save();
    ctx.translate(a.x, a.y);
    ctx.rotate(a.rotation);
    ctx.beginPath();
    const spikes = 8;
    for (let i = 0; i < spikes; i++) {
      const r = a.radius * (0.8 + 0.2 * Math.sin(i * 137.5));
      const theta = (i / spikes) * Math.PI * 2;
      const px = Math.cos(theta) * r;
      const py = Math.sin(theta) * r;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  if (gameOver) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
}

// ---------- Loop ----------

let lastTime = performance.now();

function loop(now) {
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;

  if (!gameOver) {
    updateShip(dt);
    updateBullets(dt);
    updateAsteroids(dt);
    checkCollisions();
  }

  draw();
  requestAnimationFrame(loop);
}

requestAnimationFrame(loop);
