from __future__ import annotations


def test_engineering_modules_expose_scanner_boundaries():
    from paperconan import collisions, detectors, io, schema

    assert callable(io.load_table)
    assert callable(detectors.detect_relations)
    assert callable(detectors.prefilter_relation_finding)
    assert callable(collisions.detect_collisions)
    assert schema.VALID_PROFILES == ("review", "forensic", "triage")


def test_resource_bounded_paths_have_no_superseded_helpers():
    from paperconan.fetch import _download

    obsolete = {
        "_bounded_sidecar_managed_names",
        "_pax_path_value_lengths",
        "_ReplayFile",
        "_with_replayed_tar_payload",
    }

    assert obsolete.isdisjoint(vars(_download))


def test_dense_resource_ownership_has_no_pretransaction_factory_escape():
    import ast
    from collections import Counter
    import inspect
    import textwrap

    import paperconan._audit as audit
    import pytest

    assert "_dense_detector_requirements" not in vars(audit)
    assert "_dense_detector_admission" not in vars(audit)
    assert "_run_factory" not in vars(audit._DenseFamilyResources)
    for unsafe_name in (
        "allocate",
        "allocate_candidate",
        "begin_candidate",
        "candidate",
        "complete_candidate",
        "reserve",
    ):
        assert not hasattr(audit._DenseFamilyResources, unsafe_name)

    expected_calls = {
        "detect_relations": Counter({
            "begin": 1,
            "start_candidate": 1,
        }),
        "detect_equal_pairs": Counter({
            "begin": 1,
            "start_candidate": 1,
        }),
        "detect_arithmetic_progression": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
        "detect_within_column_patterns": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
        "detect_dispersed_repeats": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
        "detect_identical_after_rounding": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
    }
    allowed_candidate_methods = {
        "allocate",
        "materialize",
        "offer",
        "release",
        "reserve",
    }
    allowed_candidate_properties = {"rejected"}
    scoped_helper_families = {
        "detect_relations",
        "detect_arithmetic_progression",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
    }

    def audit_source(source, name, expected):
        tree = ast.parse(source)
        root = tree.body[0]
        assert isinstance(root, ast.FunctionDef)
        assert ".complete_candidate(" not in source
        assert "_CandidateFindingBuffer(" not in source

        parents = {}
        calls = []
        called_attributes = set()
        resource_method_attributes = []
        candidate_names = set()
        admission_stores = set()
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
            if (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Attribute)
            ):
                called_attributes.add(id(parent.func))
                if (
                    isinstance(parent.func.value, ast.Name)
                    and parent.func.value.id == "resources"
                ):
                    calls.append(parent)
                    resource_method_attributes.append(parent.func)
                    if parent.func.attr in {
                        "start_candidate",
                        "start_allocated_candidate",
                    }:
                        assignment = parents[id(parent)]
                        assert isinstance(assignment, ast.Assign)
                        assert len(assignment.targets) == 1
                        target = assignment.targets[0]
                        if parent.func.attr == "start_candidate":
                            assert isinstance(target, ast.Name)
                            candidate_names.add(target.id)
                            admission_stores.add(id(target))
                        else:
                            assert isinstance(target, ast.Tuple)
                            assert len(target.elts) == 3
                            assert isinstance(target.elts[0], ast.Name)
                            candidate_names.add(target.elts[0].id)
                            admission_stores.add(id(target.elts[0]))

        resource_stores = [
            node
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Name)
                and node.id == "resources"
                and isinstance(node.ctx, ast.Store)
            )
        ]
        assert len(resource_stores) == 1
        resource_store = resource_stores[0]
        resource_assignment = parents[id(resource_store)]
        assert isinstance(resource_assignment, ast.Assign)
        assert resource_assignment.targets == [resource_store]

        assert len(candidate_names) == 1
        candidate_name = next(iter(candidate_names))
        candidate_method_attributes = []
        candidate_with_nodes = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Name):
                continue
            if node.id == "resources":
                if isinstance(node.ctx, ast.Store):
                    assert node is resource_store
                    continue
                assert isinstance(node.ctx, ast.Load)
                parent = parents[id(node)]
                assert isinstance(parent, ast.Attribute)
                assert parent.value is node
                assert isinstance(parent.ctx, ast.Load)
                assert id(parent) in called_attributes
                assert parent.attr in expected
            if node.id == "_resources" and isinstance(node.ctx, ast.Load):
                parent = parents[id(node)]
                assert isinstance(parent, ast.BoolOp)
                assignment = parents[id(parent)]
                assert assignment is resource_assignment
            if node.id == candidate_name:
                if isinstance(node.ctx, ast.Store):
                    assert id(node) in admission_stores
                    continue
                assert isinstance(node.ctx, ast.Load)
                parent = parents[id(node)]
                if isinstance(parent, ast.Compare):
                    assert parent.left is node
                    assert len(parent.ops) == 1
                    assert isinstance(
                        parent.ops[0], (ast.Is, ast.IsNot)
                    )
                    assert len(parent.comparators) == 1
                    comparator = parent.comparators[0]
                    assert (
                        isinstance(comparator, ast.Constant)
                        and comparator.value is None
                    )
                    continue
                if isinstance(parent, ast.withitem):
                    assert parent.context_expr is node
                    assert parent.optional_vars is None
                    with_node = parents[id(parent)]
                    assert isinstance(with_node, ast.With)
                    candidate_with_nodes.append(with_node)
                    continue
                assert isinstance(parent, ast.Attribute)
                assert parent.value is node
                assert isinstance(parent.ctx, ast.Load)
                if id(parent) in called_attributes:
                    assert parent.attr in allowed_candidate_methods
                    candidate_method_attributes.append(parent)
                else:
                    assert parent.attr in allowed_candidate_properties
                assert not parent.attr.startswith("_")

        scoped_helpers = []
        for helper in ast.walk(root):
            if helper is root or not isinstance(helper, ast.FunctionDef):
                continue
            if not any(
                isinstance(descendant, ast.Name)
                and descendant.id == candidate_name
                and isinstance(descendant.ctx, ast.Load)
                for descendant in ast.walk(helper)
            ):
                continue
            args = helper.args
            assert not args.posonlyargs
            assert not args.args
            assert args.vararg is None
            assert not args.kwonlyargs
            assert args.kwarg is None
            scoped_helpers.append(helper)

        if name in scoped_helper_families:
            assert len(scoped_helpers) == 1
            scoped_helper = scoped_helpers[0]
            assert scoped_helper.decorator_list == []
            assert scoped_helper.name not in {
                "resources",
                candidate_name,
            }
            assert not any(
                isinstance(
                    descendant,
                    (ast.Yield, ast.YieldFrom, ast.Await),
                )
                for descendant in ast.walk(scoped_helper)
            )
        else:
            assert scoped_helpers == []
            scoped_helper = root

        assert len(candidate_with_nodes) == 1
        candidate_with = candidate_with_nodes[0]
        assert len(candidate_with.items) == 1
        protected_names = {"resources", candidate_name}
        if scoped_helper is not root:
            protected_names.add(scoped_helper.name)

        for declaration in ast.walk(root):
            if isinstance(declaration, (ast.Global, ast.Nonlocal)):
                assert protected_names.isdisjoint(declaration.names)
            if isinstance(
                declaration,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                if declaration.name in protected_names:
                    assert declaration is scoped_helper
            if isinstance(declaration, ast.arg):
                assert declaration.arg not in protected_names
            if isinstance(declaration, (ast.Import, ast.ImportFrom)):
                for alias in declaration.names:
                    bound_name = (
                        alias.asname
                        or alias.name.split(".", 1)[0]
                    )
                    assert bound_name not in protected_names
            if isinstance(declaration, ast.ExceptHandler):
                assert declaration.name not in protected_names
            if isinstance(declaration, (ast.MatchAs, ast.MatchStar)):
                if declaration.name is not None:
                    assert declaration.name not in protected_names
            if isinstance(declaration, ast.MatchMapping):
                if declaration.rest is not None:
                    assert declaration.rest not in protected_names

        for deferred_node in ast.walk(root):
            if not isinstance(
                deferred_node,
                (ast.Lambda, ast.GeneratorExp, ast.AsyncFunctionDef),
            ):
                continue
            assert not any(
                isinstance(descendant, ast.Name)
                and descendant.id in protected_names
                for descendant in ast.walk(deferred_node)
            )

        for comprehension in ast.walk(root):
            if not isinstance(
                comprehension,
                (
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                continue
            assert not any(
                isinstance(descendant, ast.Name)
                and descendant.id in protected_names
                for descendant in ast.walk(comprehension)
            )

        def nearest_function(node):
            current = node
            while id(current) in parents:
                current = parents[id(current)]
                if isinstance(
                    current, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    return current
            raise AssertionError("candidate call has no function owner")

        assert nearest_function(candidate_with) is root
        if scoped_helper is not root:
            helper_references = [
                node
                for node in ast.walk(root)
                if (
                    isinstance(node, ast.Name)
                    and node.id == scoped_helper.name
                )
            ]
            assert len(helper_references) == 1
            helper_reference = helper_references[0]
            assert isinstance(helper_reference.ctx, ast.Load)
            helper_call = parents[id(helper_reference)]
            assert isinstance(helper_call, ast.Call)
            assert helper_call.func is helper_reference
            assert helper_call.args == []
            assert helper_call.keywords == []
            helper_statement = parents[id(helper_call)]
            assert isinstance(helper_statement, ast.Expr)
            assert helper_statement in candidate_with.body

        for attribute in candidate_method_attributes:
            owner = nearest_function(attribute)
            assert owner is scoped_helper
        for attribute in resource_method_attributes:
            owner = nearest_function(attribute)
            assert owner is root

        assert Counter(call.func.attr for call in calls) == expected
        assert all(
            isinstance(call.func.value, ast.Name)
            and call.func.value.id == "resources"
            for call in calls
        )

        if name == "detect_identical_after_rounding":
            start_call = next(
                call for call in calls
                if call.func.attr == "start_allocated_candidate"
            )
            initial = next(
                keyword.value
                for keyword in start_call.keywords
                if keyword.arg == "initial_reservations"
            )
            assert isinstance(initial, ast.Tuple)
            assert isinstance(initial.elts[0], ast.Tuple)
            reservation_name = initial.elts[0].elts[0]
            assert isinstance(reservation_name, ast.Constant)
            assert reservation_name.value == "candidate_workspace"

    for name, expected in expected_calls.items():
        detector = getattr(audit, name)
        parameters = inspect.signature(detector).parameters
        assert "_resources" in parameters
        assert "_state_tracker" not in parameters
        source = textwrap.dedent(inspect.getsource(detector))
        audit_source(source, name, expected)

    invalid_sources = (
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            candidate.offer("low", builder)
            def run_candidate():
                return candidate.rejected
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer(
                    "low", lambda: candidate.rejected
                )
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            candidate = replacement
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate as alias:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                return (
                    candidate.offer("low", builder)
                    for _item in values
                )
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
                yield None
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            resources = replacement
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            del resources
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = alias = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            def begin_resources():
                resources.begin()
            begin_resources()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            begin_resources = lambda: resources.begin()
            begin_resources()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            candidate.rejected = False
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            del candidate.rejected
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            saved = run_candidate
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            run_candidate()
            with candidate:
                pass
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate(candidate)
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                pass
            return run_candidate
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            [
                resources.begin()
                for _item in range(2)
            ]
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                [
                    candidate.offer("low", builder)
                    for _item in values
                ]
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            match value:
                case resources:
                    pass
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            match value:
                case [*candidate]:
                    pass
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            match value:
                case {**run_candidate}:
                    pass
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            global resources
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                nonlocal candidate
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            global run_candidate
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
    )
    for invalid_source in invalid_sources:
        with pytest.raises(AssertionError):
            audit_source(
                textwrap.dedent(invalid_source),
                "detect_relations",
                expected_calls["detect_relations"],
            )


def test_cross_sheet_pair_budget_is_owned_by_pair_helpers():
    import inspect

    import paperconan._audit as audit

    caller_source = inspect.getsource(audit.detect_collisions)
    assert ".begin_pair(" not in caller_source
    assert ".record_values(" not in caller_source
    assert "collision_value_work" not in caller_source
    assert "tail_value_work" not in caller_source

    for name in (
        "_cross_sheet_pair_stats",
        "_detect_decimal_tail_reuse_for_pair",
    ):
        helper_source = inspect.getsource(getattr(audit, name))
        assert ".begin_pair(" in helper_source
        assert ".record_values(" in helper_source


def test_axis_compact_passes_have_detector_owned_admission():
    import inspect

    import paperconan._audit as audit

    source = inspect.getsource(audit._axis_columns)
    for stage in (
        "recurrence_order",
        "recurrence_group",
        "output",
    ):
        assert f'admit_stage("{stage}"' in source
    for stage in (
        "recurrence_comparison",
        "recurrence_mark",
    ):
        assert f'admit_dynamic_stage("{stage}"' in source

    progression_source = inspect.getsource(
        audit._is_axis_progression_arrays
    )
    assert progression_source.count(
        "for index in range(len(values)):"
    ) == 1
