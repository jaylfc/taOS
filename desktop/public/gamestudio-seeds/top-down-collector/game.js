// Top-Down Collector - simple 2D canvas game, no libraries.
// Move the player around, collect gems for points, avoid the bouncing hazards.

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

// ---------- Constants ----------

const PLAYER_SIZE = 24;
const PLAYER_SPEED = 260; // px/sec
const GEM_RADIUS = 8;
const HAZARD_RADIUS = 16;
const HAZARD_SPEED = 140; // px/sec

// ---------- Input ----------

const keys = new Set();
window.addEventListener("keydown", (e) => {
  keys.add(e.code);
  if (e.code === "KeyR" && gameOver) resetGame();
});
window.addEventListener("keyup", (e) => keys.delete(e.code));

// ---------- Game state ----------

let player;
let gem;
let hazards;
let score;
let gameOver;

function randomPointAwayFrom(points, minDist) {
  // Pick a random spot inside the arena that isn't too close to existing points.
  let point;
  let attempts = 0;
  do {
    point = {
      x: 30 + Math.random() * (canvas.width - 60),
      y: 30 + Math.random() * (canvas.height - 60),
    };
    attempts += 1;
  } while (
    attempts < 50 &&
    points.some((p) => Math.hypot(p.x - point.x, p.y - point.y) < minDist)
  );
  return point;
}

function spawnGem() {
  const spot = randomPointAwayFrom([player, ...hazards], 60);
  gem = { x: spot.x, y: spot.y };
}

function resetGame() {
  player = { x: canvas.width / 2, y: canvas.height / 2 };
  hazards = [
    { x: canvas.width * 0.25, y: canvas.height * 0.25, vx: HAZARD_SPEED, vy: HAZARD_SPEED * 0.6 },
    { x: canvas.width * 0.75, y: canvas.height * 0.75, vx: -HAZARD_SPEED * 0.8, vy: -HAZARD_SPEED },
  ];
  score = 0;
  gameOver = false;
  spawnGem();
}

resetGame();

// ---------- Update ----------

function updatePlayer(dt) {
  let dx = 0;
  let dy = 0;
  if (keys.has("KeyW") || keys.has("ArrowUp")) dy -= 1;
  if (keys.has("KeyS") || keys.has("ArrowDown")) dy += 1;
  if (keys.has("KeyA") || keys.has("ArrowLeft")) dx -= 1;
  if (keys.has("KeyD") || keys.has("ArrowRight")) dx += 1;

  const len = Math.hypot(dx, dy);
  if (len > 0) {
    player.x += (dx / len) * PLAYER_SPEED * dt;
    player.y += (dy / len) * PLAYER_SPEED * dt;
  }

  const half = PLAYER_SIZE / 2;
  player.x = Math.max(half, Math.min(canvas.width - half, player.x));
  player.y = Math.max(half, Math.min(canvas.height - half, player.y));
}

function updateHazards(dt) {
  for (const h of hazards) {
    h.x += h.vx * dt;
    h.y += h.vy * dt;
    if (h.x - HAZARD_RADIUS < 0 || h.x + HAZARD_RADIUS > canvas.width) {
      h.vx *= -1;
      h.x = Math.max(HAZARD_RADIUS, Math.min(canvas.width - HAZARD_RADIUS, h.x));
    }
    if (h.y - HAZARD_RADIUS < 0 || h.y + HAZARD_RADIUS > canvas.height) {
      h.vy *= -1;
      h.y = Math.max(HAZARD_RADIUS, Math.min(canvas.height - HAZARD_RADIUS, h.y));
    }
  }
}

function checkCollisions() {
  if (Math.hypot(player.x - gem.x, player.y - gem.y) < PLAYER_SIZE / 2 + GEM_RADIUS) {
    score += 1;
    spawnGem();
  }

  for (const h of hazards) {
    if (Math.hypot(player.x - h.x, player.y - h.y) < PLAYER_SIZE / 2 + HAZARD_RADIUS) {
      gameOver = true;
    }
  }
}

// ---------- Draw ----------

function draw() {
  ctx.fillStyle = "#0a0d14";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Gem
  ctx.fillStyle = "#ffd84a";
  ctx.beginPath();
  ctx.arc(gem.x, gem.y, GEM_RADIUS, 0, Math.PI * 2);
  ctx.fill();

  // Hazards
  ctx.fillStyle = "#ff4455";
  for (const h of hazards) {
    ctx.beginPath();
    ctx.arc(h.x, h.y, HAZARD_RADIUS, 0, Math.PI * 2);
    ctx.fill();
  }

  // Player
  ctx.fillStyle = "#55aaff";
  ctx.fillRect(player.x - PLAYER_SIZE / 2, player.y - PLAYER_SIZE / 2, PLAYER_SIZE, PLAYER_SIZE);

  // Score
  ctx.fillStyle = "#ffffff";
  ctx.font = "20px -apple-system, system-ui, sans-serif";
  ctx.fillText(`Score: ${score}`, 16, 30);

  if (gameOver) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.font = "bold 32px -apple-system, system-ui, sans-serif";
    ctx.fillText(`Game Over — score: ${score}`, canvas.width / 2, canvas.height / 2 - 10);
    ctx.font = "18px -apple-system, system-ui, sans-serif";
    ctx.fillText("press R to restart", canvas.width / 2, canvas.height / 2 + 22);
    ctx.textAlign = "left";
  }
}

// ---------- Loop ----------

let lastTime = performance.now();

function loop(now) {
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;

  if (!gameOver) {
    updatePlayer(dt);
    updateHazards(dt);
    checkCollisions();
  }

  draw();
  requestAnimationFrame(loop);
}

requestAnimationFrame(loop);
