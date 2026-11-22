# record_access.R: record which rows or features an R analysis step used.
#
# The R twin of register_value.py's record_ids(). It stores only a count and
# a fingerprint of the id set (never the ids) in analysis/<name>/outputs/
# access.json, by calling the Python helper's command line, so R and Python
# produce identical records from one implementation.
#
# Vendor it next to register_value.py (for example analysis/_lib/), then:
#
#   source("analysis/_lib/record_access.R")
#   record_ids("cells_after_qc", colnames(sce), axis = "row", role = "used")
#   record_ids("genes_tested", rownames(res), axis = "feature", role = "used")
#
# ids must be character vectors: numbers format differently across
# languages, so convert them deliberately (format(x, scientific = FALSE)).
# axis: "row" (cells, samples) or "feature" (genes, variables).
# role: "read", "used", "written", or "excluded" (what a filter dropped).
#
# The helper is found through MYCELIUM_REGISTER_VALUE, else beside this file,
# else analysis/_lib/register_value.py in the project, else the plugin root
# in .mycelium/plugin-root. Python comes from MYCELIUM_PYTHON (default
# python3).

# Captured when this file is sourced, before any setwd() in the analysis.
.mycelium_access_state <- local({
  wrapper <- tryCatch(normalizePath(sys.frame(1)$ofile, mustWork = TRUE),
                      error = function(e) NULL)
  file_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
  script <- NULL
  if (length(file_arg)) {
    # Rscript shows spaces in the script path as "~+~".
    raw <- gsub("~+~", " ", sub("^--file=", "", file_arg[1]), fixed = TRUE)
    script <- normalizePath(raw, mustWork = FALSE)
  }
  list(wrapper = wrapper, script = script, launch_dir = normalizePath(getwd()))
})

record_ids <- function(name, ids, axis, role, source = NULL, namespace = NULL,
                       ordered = FALSE) {
  if (!is.character(ids)) {
    stop("record_ids: ids must be a character vector; convert them deliberately ",
         "(numbers format differently in R and Python)", call. = FALSE)
  }
  if (any(is.na(ids)) || any(ids == "")) stop("record_ids: an id is NA or empty", call. = FALSE)
  if (any(grepl("[\r\n]", ids))) stop("record_ids: an id contains a line break", call. = FALSE)
  state <- .mycelium_access_state
  project <- .mycelium_project_root(state)
  helper <- .mycelium_register_value_path(state, project)
  root <- .mycelium_analysis_root(state, project, namespace)
  if (is.null(root) || !dir.exists(root)) {
    stop("record_ids: could not find the analysis/<name> directory; pass namespace=",
         call. = FALSE)
  }
  ids_file <- tempfile("mycelium-ids-", fileext = ".txt")
  on.exit(unlink(ids_file), add = TRUE)
  writeLines(enc2utf8(ids), ids_file, useBytes = TRUE)
  args <- c(shQuote(helper), "record-ids", "--analysis-root", shQuote(root),
            "--name", shQuote(name), "--axis", shQuote(axis), "--role", shQuote(role),
            "--ids-file", shQuote(ids_file),
            "--computed-at", shQuote(if (is.null(state$script)) "R" else basename(state$script)))
  if (!is.null(source)) {
    args <- c(args, "--source", shQuote(normalizePath(source, mustWork = TRUE)))
  }
  if (isTRUE(ordered)) args <- c(args, "--ordered")
  python <- Sys.getenv("MYCELIUM_PYTHON", "python3")
  out <- suppressWarnings(system2(python, args, stdout = TRUE, stderr = TRUE))
  status <- attr(out, "status")
  if (!is.null(status) && status != 0) {
    stop(paste(c("record_ids failed:", out), collapse = "\n"), call. = FALSE)
  }
  # Success means a record came back. An older register_value.py has no
  # record-ids command and would exit 0 having written nothing.
  if (!any(grepl("\"id_set_sha256\"", out, fixed = TRUE))) {
    stop(paste(c("record_ids: register_value.py wrote no record; vendor the current",
                 "register_value.py next to record_access.R", out), collapse = "\n"),
         call. = FALSE)
  }
  invisible(out)
}

.mycelium_project_root <- function(state) {
  starts <- c(if (!is.null(state$script)) dirname(state$script), state$launch_dir)
  for (start in starts) {
    dir <- start
    repeat {
      if (dir.exists(file.path(dir, ".living"))) return(dir)
      parent <- dirname(dir)
      if (parent == dir) break
      dir <- parent
    }
  }
  NULL
}

.mycelium_register_value_path <- function(state, project) {
  candidates <- c(Sys.getenv("MYCELIUM_REGISTER_VALUE", ""),
                  if (!is.null(state$wrapper)) file.path(dirname(state$wrapper), "register_value.py"),
                  if (!is.null(project)) file.path(project, "analysis", "_lib", "register_value.py"))
  if (!is.null(project) && file.exists(file.path(project, ".mycelium", "plugin-root"))) {
    plugin <- trimws(readLines(file.path(project, ".mycelium", "plugin-root"), warn = FALSE)[1])
    candidates <- c(candidates, file.path(plugin, "skills", "core", "scripts", "register_value.py"))
  }
  for (candidate in candidates) {
    if (nzchar(candidate) && file.exists(candidate)) return(normalizePath(candidate))
  }
  stop("record_ids: register_value.py not found (set MYCELIUM_REGISTER_VALUE)", call. = FALSE)
}

.mycelium_analysis_root <- function(state, project, namespace) {
  if (!is.null(namespace)) {
    if (is.null(project)) return(NULL)
    return(file.path(project, "analysis", namespace))
  }
  if (is.null(state$script)) return(NULL)
  parts <- strsplit(state$script, "/", fixed = TRUE)[[1]]
  for (i in rev(which(parts == "analysis"))) {
    if (i < length(parts) - 1) return(paste(parts[seq_len(i + 1)], collapse = "/"))
  }
  NULL
}
