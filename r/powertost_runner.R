args <- commandArgs(trailingOnly = TRUE)

parse_args <- function(args) {
  res <- list()
  i <- 1
  while (i <= length(args)) {
    key <- args[i]
    if (startsWith(key, "--")) {
      val <- args[i + 1]
      res[[substring(key, 3)]] <- val
      i <- i + 2
    } else {
      i <- i + 1
    }
  }
  res
}

params <- parse_args(args)

design <- params[["design"]]
cv <- as.numeric(params[["cv"]])
power <- as.numeric(params[["power"]])
alpha <- as.numeric(params[["alpha"]])

if (is.na(cv) || is.na(power) || is.na(alpha)) {
  cat('{"error": "Invalid parameters"}')
  quit(status = 1)
}

if (!requireNamespace("PowerTOST", quietly = TRUE)) {
  cat('{"error": "PowerTOST package not installed"}')
  quit(status = 1)
}

suppressMessages(library(PowerTOST))

# PowerTOST expects CV as fraction (0.3 for 30%)
cv_frac <- cv / 100

# Use mapped design from caller (default to 2x2)
pt_design <- ifelse(is.null(design) || design == "", "2x2", design)

# Suppress verbose output from PowerTOST
res <- suppressWarnings({
  capture.output(tmp <- sampleN.TOST(
    CV = cv_frac,
    theta0 = 1,
    theta1 = 0.8,
    theta2 = 1.25,
    targetpower = power,
    alpha = alpha,
    design = pt_design
  ))
  tmp
})

n_total <- as.integer(res[["Sample size"]][1])
if (is.na(n_total)) {
  cat('{"error": "Failed to compute N"}')
  quit(status = 1)
}

if (!requireNamespace("jsonlite", quietly = TRUE)) {
  cat(sprintf('{"N_total": %d}', n_total))
} else {
  suppressMessages(library(jsonlite))
  cat(toJSON(list(N_total = n_total), auto_unbox = TRUE))
}
