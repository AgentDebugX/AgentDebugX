from __future__ import annotations

import re

from agentdebug.diagnose.attribute import BinarySearchAttributor
from agentdebug.schema import AgentEvent, AgentTrajectory


def _event(i: int, *, marker: bool = False) -> AgentEvent:
    return AgentEvent(
        event_id=f'evt_{i:02d}',
        trace_id='t',
        agent_name='solver',
        event_type='tool.call',
        step_index=i,
        input={'tool': 'goto', 'args': {}},
        output='CONSTRAINT ESTABLISHED' if marker else f'step {i} ok',
        error=None,
    )


def _trajectory(n: int, *, marker_at: int | None = None) -> AgentTrajectory:
    return AgentTrajectory(
        trace_id='t',
        task_id='k',
        goal='g',
        framework='f',
        events=[_event(i, marker=(i == marker_at)) for i in range(n)],
    )


def _renderer(**kwargs: object) -> BinarySearchAttributor:
    """A BinarySearchAttributor with only the rendering state set, so no LLM is needed."""
    a = BinarySearchAttributor.__new__(BinarySearchAttributor)
    a.context_window = kwargs.get('context_window', 6)  # type: ignore[assignment]
    pinned = kwargs.get('always_include_steps') or ()
    a.always_include_steps = frozenset(pinned)  # type: ignore[arg-type,assignment]
    return a


def _visible_steps(rendered: str) -> list[int]:
    return [int(x) for x in re.findall(r'step=(\d+)', rendered)]


def test_the_default_window_hides_the_middle_of_a_long_trajectory() -> None:
    """Documents the behaviour the options below exist to work around.

    The elision is positional, not relevance-based: at the default window of 6 a
    30-event trajectory shows only steps 0-5 and 24-29, so a constraint established at
    step 10 is invisible however decisive it is.
    """
    traj = _trajectory(30, marker_at=10)
    rendered = _renderer()._render_prefix(traj)

    assert _visible_steps(rendered) == [0, 1, 2, 3, 4, 5, 24, 25, 26, 27, 28, 29]
    assert 'CONSTRAINT ESTABLISHED' not in rendered
    assert '18 events elided' in rendered


def test_short_trajectories_are_never_elided() -> None:
    traj = _trajectory(12, marker_at=6)
    rendered = _renderer()._render_prefix(traj)

    assert _visible_steps(rendered) == list(range(12))
    assert 'elided' not in rendered


def test_pinning_a_step_makes_it_visible_without_widening_the_window() -> None:
    traj = _trajectory(30, marker_at=10)
    rendered = _renderer(always_include_steps=[10])._render_prefix(traj)

    assert 'CONSTRAINT ESTABLISHED' in rendered
    # Pinned events are restored in trajectory order, so step index stays monotone.
    assert _visible_steps(rendered) == [0, 1, 2, 3, 4, 5, 10, 24, 25, 26, 27, 28, 29]
    # And the ellipsis count drops by exactly what was restored, so the note stays true.
    assert '17 events elided' in rendered


def test_pinning_several_steps_keeps_them_ordered() -> None:
    traj = _trajectory(40)
    rendered = _renderer(always_include_steps=[20, 8, 15])._render_prefix(traj)

    visible = _visible_steps(rendered)
    assert [8, 15, 20] == [s for s in visible if s in (8, 15, 20)]
    assert visible == sorted(visible), 'rendered prefix must stay monotone in step index'


def test_pinning_a_step_already_in_the_window_does_not_duplicate_it() -> None:
    traj = _trajectory(30)
    rendered = _renderer(always_include_steps=[2, 27])._render_prefix(traj)

    visible = _visible_steps(rendered)
    assert visible.count(2) == 1
    assert visible.count(27) == 1
    assert '18 events elided' in rendered, 'nothing was restored, so the count is unchanged'


def test_pinning_a_step_that_does_not_exist_is_harmless() -> None:
    traj = _trajectory(30)
    rendered = _renderer(always_include_steps=[999])._render_prefix(traj)

    assert _visible_steps(rendered) == [0, 1, 2, 3, 4, 5, 24, 25, 26, 27, 28, 29]


def test_context_window_none_disables_elision() -> None:
    traj = _trajectory(30, marker_at=10)
    rendered = _renderer(context_window=None)._render_prefix(traj)

    assert _visible_steps(rendered) == list(range(30))
    assert 'CONSTRAINT ESTABLISHED' in rendered
    assert 'elided' not in rendered


def test_the_elision_report_says_what_was_withheld() -> None:
    """A caller must be able to tell "considered and dismissed" from "never seen"."""
    traj = _trajectory(30, marker_at=10)

    _, report = _renderer()._prefix_view(list(traj.events))
    assert report['elided'] is True
    assert report['events_total'] == 30
    assert report['events_shown'] == 12
    assert report['events_elided'] == 18
    assert report['context_window'] == 6
    assert report['pinned_steps'] == []
    assert report['restored_by_pinning'] == 0

    _, pinned_report = _renderer(always_include_steps=[10])._prefix_view(list(traj.events))
    assert pinned_report['events_elided'] == 17
    assert pinned_report['pinned_steps'] == [10]
    assert pinned_report['restored_by_pinning'] == 1

    _, whole = _renderer(context_window=None)._prefix_view(list(traj.events))
    assert whole['elided'] is False
    assert whole['events_shown'] == 30


def test_report_and_rendering_never_disagree_about_the_count() -> None:
    """The ellipsis note in the prompt and the machine-readable report must match."""
    for n in (5, 12, 13, 30, 41):
        for pinned in ((), (7,), (7, 9, 20)):
            r = _renderer(always_include_steps=pinned)
            rendered = r._render_prefix(_trajectory(n))
            _, report = r._prefix_view(list(_trajectory(n).events))
            note = re.search(r'\((\d+) events elided\)', rendered)
            if report['elided']:
                assert note is not None, f'n={n} pinned={pinned}: report says elided, prompt does not'
                assert int(note.group(1)) == report['events_elided']
            else:
                assert note is None, f'n={n} pinned={pinned}: prompt elides, report says it did not'
            assert len(_visible_steps(rendered)) == report['events_shown']


def test_constructor_defaults_are_unchanged() -> None:
    """Existing callers must see byte-identical rendering; these options are opt-in."""
    import inspect

    sig = inspect.signature(BinarySearchAttributor.__init__)
    assert sig.parameters['context_window'].default == 6
    assert sig.parameters['always_include_steps'].default is None
