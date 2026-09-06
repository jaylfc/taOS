// Endless Runner - side-scrolling jumper, plain canvas 2D, no libraries.
// Jump over obstacles as the pace keeps climbing. Distance is the score.

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

// ---------- Constants ----------

const PLAYER_X = 110;
const PLAYER_SIZE = 36;
const GRAVITY = 2200;
const JUMP_SPEED = 780;
const BASE_SPEED = 320; // px/sec, ground scroll speed
const SPEED_RAMP = 6; // px/sec gained per second survived
const MIN_GAP = 0.7; // seconds
const MAX_GAP = 1.5; // seconds

function groundY() {
  return canvas.height * 0.75;
}

// ---------- Input ----------

let jumpQueued = false;

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" || e.code === "ArrowUp" || e.code === "KeyW") {
    e.preventDefault();
    jumpQueued = true;
  }
  if (e.code === "KeyR" && gameOver) resetGame();
});

window.addEventListener("pointerdown", () => {
  if (gameOver) {
    resetGame();
  } else {
    jumpQueued = true;
  }
});

// ---------- Game state ----------

let player;
let obstacles;
let speed;
let elapsed;
let nextSpawnIn;
let score;
let gameOver;

function resetGame() {
  player = { y: groundY() - PLAYER_SIZE, vy: 0, onGround: true };
  obstacles = [];
  speed = BASE_SPEED;
  elapsed = 0;
  nextSpawnIn = MIN_GAP + Math.random() * (MAX_GAP - MIN_GAP);
  score = 0;
  gameOver = false;
}

resetGame();

// ---------- Update ----------

function spawnObstacle() {
  const height = 30 + Math.random() * 40;
  obstacles.push({ x: canvas.width + 20, width: 26, height, passed: false });
  nextSpawnIn = MIN_GAP + Math.random() * (MAX_GAP - MIN_GAP);
}

function updatePlayer(dt) {
  if (jumpQueued && player.onGround) {
    player.vy = -JUMP_SPEED;
    player.onGround = false;
  }
  jumpQueued = false;

  player.vy += GRAVITY * dt;
  player.y += player.vy * dt;

  const floor = groundY() - PLAYER_SIZE;
  if (player.y >= floor) {
    player.y = floor;
    player.vy = 0;
    player.onGround = true;
  }
}

function updateObstacles(dt) {
  for (const o of obstacles) {
    o.x -= speed * dt;
    if (!o.passed && o.x + o.width < PLAYER_X) {
      o.passed = true;
    }
  }
  while (obstacles.length && obstacles[0].x + obstacles[0].width < -20) {
    obstacles.shift();
  }
}

function checkCollisions() {
  const px1 = PLAYER_X;
  const px2 = PLAYER_X + PLAYER_SIZE;
  const py1 = player.y;
  const py2 = player.y + PLAYER_SIZE;
  for (const o of obstacles) {
    const oy2 = groundY();
    const oy1 = oy2 - o.height;
    if (px2 > o.x && px1 < o.x + o.width && py2 > oy1 && py1 < oy2) {
      gameOver = true;
      break;
    }
  }
}

// ---------- Draw ----------

function draw() {
  ctx.fillStyle = "#14101f";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Ground line
  ctx.strokeStyle = "#4a3f66";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(0, groundY());
  ctx.lineTo(canvas.width, groundY());
  ctx.stroke();

  // Obstacles
  ctx.fillStyle = "#ff5fa8";
  for (const o of obstacles) {
    ctx.fillRect(o.x, groundY() - o.height, o.width, o.height);
  }

  // Player
  ctx.fillStyle = "#8fd6ff";
  ctx.fillRect(PLAYER_X, player.y, PLAYER_SIZE, PLAYER_SIZE);

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
    ctx.fillText("press R or tap to restart", canvas.width / 2, canvas.height / 2 + 22);
    ctx.textAlign = "left";
  }
}

// ---------- Loop ----------

let lastTime = performance.now();

function loop(now) {
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;

  if (!gameOver) {
    elapsed += dt;
    speed = BASE_SPEED + elapsed * SPEED_RAMP;
    score = Math.floor(elapsed * 10);

    updatePlayer(dt);
    updateObstacles(dt);

    nextSpawnIn -= dt;
    if (nextSpawnIn <= 0) spawnObstacle();

    checkCollisions();
  }

  draw();
  requestAnimationFrame(loop);
}

requestAnimationFrame(loop);
