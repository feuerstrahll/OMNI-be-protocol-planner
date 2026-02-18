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

lower <- suppressWarnings(as.numeric(params[["lower"]]))
upper <- suppressWarnings(as.numeric(params[["upper"]]))
n <- suppressWarnings(as.numeric(params[["n"]]))
design <- params[["design"]]
if (is.null(design) || design == "") {
  design <- "2x2"
}

warnings <- c()

emit <- function(cv, warnings) {
  if (requireNamespace("jsonlite", quietly = TRUE)) {
    suppressMessages(library(jsonlite))
    cat(toJSON(list(cv = cv, warnings = warnings), auto_unbox = TRUE))
  } else {
    warn_json <- paste(sprintf("\"%s\"", warnings), collapse = ",")
    cv_json <- if (is.null(cv)) "null" else sprintf("%.6f", cv)
    cat(sprintf("{\"cv\": %s, \"warnings\": [%s]}", cv_json, warn_json))
  }
}

if (is.na(lower) || is.na(upper) || is.na(n)) {
  emit(NULL, c("invalid_parameters"))
  quit(status = 0)
}

if (!requireNamespace("PowerTOST", quietly = TRUE)) {
  emit(NULL, c("powertost_not_installed"))
  quit(status = 0)
}

suppressMessages(library(PowerTOST))

cv <- NULL
tryCatch({
  cv <- CVfromCI(lower = lower, upper = upper, n = n, design = design)
}, error = function(e) {
  warnings <<- c(warnings, "cvfromci_failed")
  cv <<- NULL
})

if (is.null(cv) || is.na(cv) || is.infinite(cv)) {
  emit(NULL, c(warnings, "cvfromci_invalid"))
  quit(status = 0)
}

if (cv <= 1) {
  cv <- cv * 100
  warnings <- c(warnings, "cv_assumed_fraction")
}

emit(cv, warnings)
