// Breakout - classic paddle-and-ball brick breaker, plain canvas 2D.

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  layoutBricks();
}
window.addEventListener("resize", resizeCanvas);

// ---------- Constants ----------

const ROWS = 5;
const COLS = 8;
const BRICK_GAP = 6;
const BRICK_TOP_MARGIN = 60;
const BRICK_SIDE_MARGIN = 20;
const BRICK_HEIGHT = 22;
const ROW_COLORS = ["#ff5566", "#ff9955", "#ffd84a", "#66cc66", "#55aaff"];

const PADDLE_WIDTH = 110;
const PADDLE_HEIGHT = 14;
const PADDLE_SPEED = 640; // px/sec, for keyboard control
const BALL_RADIUS = 8;
const BASE_BALL_SPEED = 340;

// ---------- State ----------

let bricks = []; // { x, y, w, h, color, alive }
let paddle;
let ball;
let score;
let lives;
let gameOver;
let win;

function layoutBricks() {
  bricks = [];
  const totalGap = BRICK_GAP * (COLS - 1);
  const brickWidth = (canvas.width - BRICK_SIDE_MARGIN * 2 - totalGap) / COLS;
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) {
      bricks.push({
        x: BRICK_SIDE_MARGIN + col * (brickWidth + BRICK_GAP),
        y: BRICK_TOP_MARGIN + row * (BRICK_HEIGHT + BRICK_GAP),
        w: brickWidth,
        h: BRICK_HEIGHT,
        color: ROW_COLORS[row % ROW_COLORS.length],
        alive: true,
      });
    }
  }
}

function resetBall() {
  ball = {
    x: canvas.width / 2,
    y: canvas.height - 80,
    vx: BASE_BALL_SPEED * 0.4,
    vy: -BASE_BALL_SPEED,
  };
}

function resetGame() {
  paddle = { x: canvas.width / 2 - PADDLE_WIDTH / 2, y: canvas.height - 40 };
  score = 0;
  lives = 3;
  gameOver = false;
  win = false;
  layoutBricks();
  resetBall();
}

resizeCanvas();
resetGame();

// ---------- Input ----------

const keys = new Set();
window.addEventListener("keydown", (e) => {
  keys.add(e.code);
  if (e.code === "KeyR" && (gameOver || win)) resetGame();
});
window.addEventListener("keyup", (e) => keys.delete(e.code));

window.addEventListener("mousemove", (e) => {
  paddle.x = e.clientX - PADDLE_WIDTH / 2;
});
canvas.addEventListener("click", () => {
  if (gameOver || win) resetGame();
});

// ---------- Update ----------

function updatePaddle(dt) {
  if (keys.has("ArrowLeft")) paddle.x -= PADDLE_SPEED * dt;
  if (keys.has("ArrowRight")) paddle.x += PADDLE_SPEED * dt;
  paddle.x = Math.max(0, Math.min(canvas.width - PADDLE_WIDTH, paddle.x));
}

function updateBall(dt) {
  ball.x += ball.vx * dt;
  ball.y += ball.vy * dt;

  // Walls
  if (ball.x - BALL_RADIUS < 0) {
    ball.x = BALL_RADIUS;
    ball.vx *= -1;
  } else if (ball.x + BALL_RADIUS > canvas.width) {
    ball.x = canvas.width - BALL_RADIUS;
    ball.vx *= -1;
  }
  if (ball.y - BALL_RADIUS < 0) {
    ball.y = BALL_RADIUS;
    ball.vy *= -1;
  }

  // Paddle collision (only while ball is moving downward)
  if (
    ball.vy > 0 &&
    ball.y + BALL_RADIUS >= paddle.y &&
    ball.y + BALL_RADIUS <= paddle.y + PADDLE_HEIGHT + 6 &&
    ball.x >= paddle.x - BALL_RADIUS &&
    ball.x <= paddle.x + PADDLE_WIDTH + BALL_RADIUS
  ) {
    const hitOffset = (ball.x - (paddle.x + PADDLE_WIDTH / 2)) / (PADDLE_WIDTH / 2); // -1..1
    const speed = Math.hypot(ball.vx, ball.vy);
    ball.vx = hitOffset * speed;
    ball.vy = -Math.sqrt(Math.max(speed * speed - ball.vx * ball.vx, (speed * 0.5) ** 2));
    ball.y = paddle.y - BALL_RADIUS;
  }

  // Brick collisions
  for (const brick of bricks) {
    if (!brick.alive) continue;
    if (
      ball.x + BALL_RADIUS > brick.x &&
      ball.x - BALL_RADIUS < brick.x + brick.w &&
      ball.y + BALL_RADIUS > brick.y &&
      ball.y - BALL_RADIUS < brick.y + brick.h
    ) {
      brick.alive = false;
      ball.vy *= -1;
      score += 10;
      break;
    }
  }

  if (bricks.every((b) => !b.alive)) {
    win = true;
  }

  // Fell below paddle
  if (ball.y - BALL_RADIUS > canvas.height) {
    lives -= 1;
    if (lives <= 0) {
      gameOver = true;
    } else {
      resetBall();
    }
  }
}

// ---------- Draw ----------

function draw() {
  ctx.fillStyle = "#0a0d14";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (const brick of bricks) {
    if (!brick.alive) continue;
    ctx.fillStyle = brick.color;
    ctx.fillRect(brick.x, brick.y, brick.w, brick.h);
  }

  ctx.fillStyle = "#55aaff";
  ctx.fillRect(paddle.x, paddle.y, PADDLE_WIDTH, PADDLE_HEIGHT);

  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(ball.x, ball.y, BALL_RADIUS, 0, Math.PI * 2);
  ctx.fill();

  ctx.font = "20px -apple-system, system-ui, sans-serif";
  ctx.fillText(`Score: ${score}`, 16, 30);
  ctx.fillText(`Lives: ${lives}`, canvas.width - 110, 30);

  if (gameOver || win) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.font = "bold 32px -apple-system, system-ui, sans-serif";
    ctx.fillText(win ? "You win!" : "Game Over", canvas.width / 2, canvas.height / 2 - 10);
    ctx.font = "18px -apple-system, system-ui, sans-serif";
    ctx.fillText("press R or click to restart", canvas.width / 2, canvas.height / 2 + 22);
    ctx.textAlign = "left";
  }
}

// ---------- Loop ----------

let lastTime = performance.now();

function loop(now) {
  const dt = Math.min((now - lastTime) / 1000, 0.05);
  lastTime = now;

  if (!gameOver && !win) {
    updatePaddle(dt);
    updateBall(dt);
  }

  draw();
  requestAnimationFrame(loop);
}

requestAnimationFrame(loop);
