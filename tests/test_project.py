r"""The project index behind the editor's completions.

Everything here runs against a real git repository holding the awkward things a
real paper contains: a caption with the label inside it, a bibliography entry
whose title is armoured with nested braces, a macro with an optional argument,
and prose in a file that is not `main.tex`. If any of these is read wrongly the
completion list quietly offers the wrong key, which is worse than offering none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from galley.services import project

METHOD = r"""\section{The method}
\label{sec:method}

We work in \(\lambda\)-calculus.  % \label{sec:commented-out}

\subsection{Losses}
\label{sec:method:losses}

\begin{figure}[t]
  \includegraphics{figures/loss.pdf}
  \caption{Training loss on the \emph{held-out} split, with the
    baseline drawn for reference.\label{fig:loss}}
\end{figure}

\begin{equation}
  E = mc^2
  \label{eq:energy}
\end{equation}
"""

RESULTS = r"""\section{Results}
\label{sec:results}

\begin{table}[t]
  \begin{tabular}{ll}
    channel & rate \\
  \end{tabular}
  \caption{The three-channel sweep, every cell scored.}
  \label{tab:sweep}
\end{table}

Cited here: \citep{azerbayev2024llemma} and \cref{fig:loss}.
"""

REFS = r"""% A bibliography with the shapes BibTeX actually allows.
@string{iclr = {International Conference on Learning Representations}}

@comment{Ignore me, and the {nested} braces I contain.}

@inproceedings{azerbayev2024llemma,
  author    = {Azerbayev, Zhangir and Schoelkopf, Hailey and others},
  title     = {{Llemma}: An Open Language Model For {Mathematics}},
  booktitle = iclr,
  year      = {2024}
}

@article{coquand1988calculus,
  author  = "Coquand, Thierry and Huet, G{\'e}rard",
  title   = "The Calculus of {Constructions}",
  journal = "Information and Computation",
  year    = 1988
}

@misc{yeh2026wrapped,
  author       = {Yeh, Po Hung and
                  Someone, Else},
  title        = {A title that is
                  wrapped across lines},
  year         = {2026},
  howpublished = {arXiv:2601.00001},
}

@misc{glued2020,
  title = {Part one} # { and part two},
  year  = {2020}
}

@misc{accented2026,
  author = {Martin-L\"{o}f, Per and Del\'etang, Gr\'egoire and Stra\ss e, J\"urgen},
  title  = {Ma\~nana, na\"{\i}ve, and \v{C}apek},
  year   = {2026}
}
"""

MACROS = r"""\ProvidesPackage{qlmacros}[2026/09/09 the project's own names]
% \newcommand{\commentedout}{never offered}
\newcommand{\qlambda}{Q$\Lambda$\xspace}
\newcommand{\KL}[2]{\mathrm{KL}(#1\|#2)}
\newcommand{\cell}[3][term]{#1/#2/#3}
\newcommand\bare[1]{#1}
\providecommand{\eps}{\varepsilon}
\renewcommand{\vec}[1]{\mathbf{#1}}
\DeclareMathOperator{\argmax}{arg\,max}
\DeclareMathOperator*{\Ex}{\mathbb{E}}
\newenvironment{shadowbox}[1][]{\begin{quote}}{\end{quote}}
\def\packdir{data/packed}
\def\pair#1#2{(#1,#2)}
\newcommand{\flm@internal}{a package's own business}
"""


@pytest.fixture
def project_repo(paper_repo: Path, git_helper) -> Path:
    """A small paper with every awkward construct these tests care about."""
    (paper_repo / "sections").mkdir()
    (paper_repo / "sections" / "method.tex").write_text(METHOD)
    (paper_repo / "sections" / "results.tex").write_text(RESULTS)
    (paper_repo / "bib").mkdir()
    (paper_repo / "bib" / "refs.bib").write_text(REFS)
    (paper_repo / "qlmacros.sty").write_text(MACROS)
    git_helper(paper_repo, "add", "-A")
    git_helper(paper_repo, "commit", "-qm", "a paper worth indexing")
    return paper_repo


def label(index: project.ProjectIndex, key: str) -> project.Label:
    return next(x for x in index.labels if x.key == key)


def citation(index: project.ProjectIndex, key: str) -> project.Citation:
    return next(x for x in index.citations if x.key == key)


def macro(index: project.ProjectIndex, name: str) -> project.Macro:
    return next(x for x in index.macros if x.name == name)


# -- labels ------------------------------------------------------------------


def test_a_label_after_a_heading_carries_the_heading(project_repo: Path) -> None:
    index = project.index(project_repo)
    assert label(index, "sec:method").context == "The method"
    assert label(index, "sec:method").kind == "section"
    assert label(index, "sec:method:losses").context == "Losses"
    assert label(index, "sec:method:losses").kind == "subsection"


def test_a_label_inside_a_caption_carries_the_caption(project_repo: Path) -> None:
    found = label(project.index(project_repo), "fig:loss")
    assert found.kind == "figure"
    assert found.context == (
        "Training loss on the \\emph{held-out} split, with the "
        "baseline drawn for reference."
    )


def test_a_label_after_a_caption_carries_it_too(project_repo: Path) -> None:
    """The other half of the paper's habit: caption first, label beneath it."""
    found = label(project.index(project_repo), "tab:sweep")
    assert found.kind == "table"
    assert found.context == "The three-channel sweep, every cell scored."


def test_a_label_with_no_caption_falls_back_to_its_section(project_repo: Path) -> None:
    found = label(project.index(project_repo), "eq:energy")
    assert found.kind == "equation"
    assert found.context == "Losses"


def test_a_label_knows_which_file_and_line_it_is_on(project_repo: Path) -> None:
    found = label(project.index(project_repo), "sec:results")
    assert found.file == "sections/results.tex"
    assert found.line == 2


def test_a_commented_out_label_is_not_in_the_project(project_repo: Path) -> None:
    keys = [x.key for x in project.index(project_repo).labels]
    assert "sec:commented-out" not in keys


def test_a_label_git_ignores_is_not_in_the_project(project_repo: Path) -> None:
    (project_repo / ".gitignore").write_text("build/\n")
    (project_repo / "build").mkdir()
    (project_repo / "build" / "scratch.tex").write_text("\\label{sec:scratch}\n")
    keys = [x.key for x in project.index(project_repo).labels]
    assert "sec:scratch" not in keys


def test_the_same_key_in_two_files_is_reported_twice(project_repo: Path) -> None:
    """A paper with an `archive/` has these, and you want to see which is which."""
    (project_repo / "sections" / "older.tex").write_text(
        "\\section{An earlier method}\n\\label{sec:method}\n"
    )
    sites = [x for x in project.index(project_repo).labels if x.key == "sec:method"]
    assert sorted(x.file for x in sites) == [
        "sections/method.tex",
        "sections/older.tex",
    ]


# -- citations ---------------------------------------------------------------


def test_a_braced_title_loses_its_capitalisation_armour(project_repo: Path) -> None:
    entry = citation(project.index(project_repo), "azerbayev2024llemma")
    assert entry.title == "Llemma: An Open Language Model For Mathematics"
    assert entry.author == "Azerbayev, Zhangir and Schoelkopf, Hailey and others"
    assert entry.year == "2024"
    assert entry.entry_type == "inproceedings"


def test_a_quoted_entry_reads_the_same_as_a_braced_one(project_repo: Path) -> None:
    entry = citation(project.index(project_repo), "coquand1988calculus")
    assert entry.title == "The Calculus of Constructions"
    assert entry.year == "1988", "a bare number is a value too"


def test_a_field_wrapped_across_lines_comes_back_as_one_line(
    project_repo: Path,
) -> None:
    entry = citation(project.index(project_repo), "yeh2026wrapped")
    assert entry.title == "A title that is wrapped across lines"
    assert entry.author == "Yeh, Po Hung and Someone, Else"


def test_concatenated_values_are_joined(project_repo: Path) -> None:
    assert citation(project.index(project_repo), "glued2020").title == (
        "Part one and part two"
    )


def test_tex_accents_become_letters_you_can_read(project_repo: Path) -> None:
    """A name you cannot read is a name you cannot recognise in a list."""
    entry = citation(project.index(project_repo), "accented2026")
    assert entry.author == "Martin-Löf, Per and Delétang, Grégoire and Straße, Jürgen"
    assert entry.title == "Mañana, naïve, and Čapek"


def test_a_quoted_field_carries_its_accents_too(project_repo: Path) -> None:
    entry = citation(project.index(project_repo), "coquand1988calculus")
    assert entry.author == "Coquand, Thierry and Huet, Gérard"


def test_string_and_comment_entries_are_not_citable(project_repo: Path) -> None:
    keys = [x.key for x in project.index(project_repo).citations]
    assert "iclr" not in keys
    assert len(keys) == 5


def test_a_string_macro_is_expanded_where_it_is_used(project_repo: Path) -> None:
    entry = citation(project.index(project_repo), "azerbayev2024llemma")
    assert entry.key == "azerbayev2024llemma"
    # The venue is not carried on the citation, but resolving it must not have
    # derailed the fields after it.
    assert entry.year == "2024"


def test_a_citation_knows_where_it_is_defined(project_repo: Path) -> None:
    entry = citation(project.index(project_repo), "coquand1988calculus")
    assert entry.file == "bib/refs.bib"
    assert entry.line == 13


# -- macros ------------------------------------------------------------------


def test_a_macro_reports_how_many_arguments_it_takes(project_repo: Path) -> None:
    index = project.index(project_repo)
    assert (macro(index, "qlambda").args, macro(index, "qlambda").optional) == (0, False)
    assert (macro(index, "KL").args, macro(index, "KL").optional) == (2, False)
    assert (macro(index, "cell").args, macro(index, "cell").optional) == (3, True)
    assert macro(index, "bare").args == 1, "the name may be written without braces"


def test_every_way_of_defining_a_command_is_read(project_repo: Path) -> None:
    index = project.index(project_repo)
    for name in ("eps", "vec", "argmax", "Ex", "packdir"):
        assert macro(index, name).kind == "command"
    assert macro(index, "argmax").args == 0, "its second argument is the printed form"
    assert macro(index, "pair").args == 2, "a \\def counts its parameter text"


def test_an_environment_is_indexed_as_one(project_repo: Path) -> None:
    found = macro(project.index(project_repo), "shadowbox")
    assert found.kind == "environment"
    assert (found.args, found.optional) == (1, True)


def test_a_packages_internal_names_are_not_offered(project_repo: Path) -> None:
    names = [x.name for x in project.index(project_repo).macros]
    assert "flm@internal" not in names, "you cannot type one without \\makeatletter"
    assert "commentedout" not in names


def test_macros_are_found_in_the_projects_own_package(project_repo: Path) -> None:
    assert macro(project.index(project_repo), "qlambda").file == "qlmacros.sty"


# -- the cache ---------------------------------------------------------------


def test_asking_twice_costs_nothing(project_repo: Path) -> None:
    first = project.index(project_repo)
    assert project.index(project_repo) is first


def test_editing_a_file_rebuilds_the_index(project_repo: Path) -> None:
    first = project.index(project_repo)
    path = project_repo / "sections" / "results.tex"
    path.write_text(path.read_text() + "\n\\subsection{Late addition}\n\\label{sec:late}\n")
    second = project.index(project_repo)
    assert second is not first
    assert label(second, "sec:late").context == "Late addition"


def test_a_new_file_rebuilds_the_index(project_repo: Path) -> None:
    first = project.index(project_repo)
    (project_repo / "appendix.tex").write_text("\\section{Appendix}\n\\label{app:a}\n")
    assert project.index(project_repo) is not first


# -- the route ---------------------------------------------------------------


def test_the_route_serves_the_index(client, project_repo: Path) -> None:
    body = client.get("/api/project/index").json()
    assert {x["key"] for x in body["labels"]} >= {"sec:method", "fig:loss", "tab:sweep"}
    assert {x["key"] for x in body["citations"]} >= {"azerbayev2024llemma"}
    assert {x["name"] for x in body["macros"]} >= {"qlambda", "KL", "shadowbox"}
    assert body["files"] == 5


def test_the_route_indexes_a_session_checkout(client, project_repo: Path) -> None:
    """A session edits its own worktree, so it must be indexed from there."""
    row = client.post("/api/sessions", json={"prompt": "index me", "start": False}).json()
    body = client.get(
        "/api/project/index", params={"session_id": row["id"]}
    ).json()
    assert {x["key"] for x in body["labels"]} >= {"sec:method", "tab:sweep"}
