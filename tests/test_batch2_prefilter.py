def _collector():
    from paperconan import detectors

    return detectors


def test_prefilter_drops_named_spectral_axis_even_when_values_not_arithmetic():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "identical_column",
        "m/z",
        "m/z",
        1000,
        1.0,
        "col[2] == col[0]",
        [100.001, 101.037, 103.2, 108.9, 119.4],
        [100.001, 101.037, 103.2, 108.9, 119.4],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "shared_axis"
    assert f["flags"]["is_axis"] is True


def test_prefilter_downweights_pvalue_to_qvalue_derived_statistic():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "P.Value",
        "adj.P.Val",
        500,
        1.0,
        "col[4] = 1 * col[3] + 0",
        [0.001, 0.02, 0.2, 0.7, 1.0],
        [0.001, 0.02, 0.2, 0.7, 1.0],
    )

    assert f["prefilter"] == "downweight"
    assert f["prefilter_reason"] == "derived_statistical_column"


def test_prefilter_uses_specific_reason_for_complement_percentages():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "sum_constant",
        "epithelium %",
        "stroma %",
        30,
        1.0,
        "col[1] + col[2] = 100",
        [20.0, 30.0, 40.0, 45.0, 55.0],
        [80.0, 70.0, 60.0, 55.0, 45.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "complement_percentage_sum_to_100"


def test_prefilter_drops_fraction_complements_that_sum_to_one():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "sum_constant",
        "CCR6+ fraction",
        "CCR6- fraction",
        40,
        1.0,
        "col[1] + col[2] = 1",
        [0.2, 0.35, 0.4, 0.55, 0.7],
        [0.8, 0.65, 0.6, 0.45, 0.3],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "complement_fraction_sum_to_constant"


def test_prefilter_drops_normalized_complements_that_sum_to_two():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "sum_constant",
        "Vehicle_1 normalized",
        "Vehicle_2 normalized",
        24,
        1.0,
        "col[1] + col[2] = 2",
        [0.9, 1.1, 0.82, 1.25, 0.77],
        [1.1, 0.9, 1.18, 0.75, 1.23],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "complement_fraction_sum_to_constant"


def test_prefilter_drops_explicit_qpcr_delta_ct_formula_columns():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "dCT",
        "ddCT",
        30,
        1.0,
        "col[4] = 1 * col[3] + -2.1",
        [3.1, 2.9, 4.2, 3.7, 5.0],
        [1.0, 0.8, 2.1, 1.6, 2.9],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "qpcr_formula_derived_column"


def test_prefilter_drops_explicit_sem_sd_scaling():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "SD",
        "SEM",
        12,
        1.0,
        "col[2] = col[1] * 0.353553",
        [2.0, 3.2, 4.7, 5.1, 6.8],
        [0.707106, 1.1313696, 1.6616991, 1.8031203, 2.4041604],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "summary_statistic_scaling"


def test_prefilter_drops_image_processing_derived_columns():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "Gray_Value",
        "Gray/max",
        200,
        1.0,
        "col[2] = col[1] * 0.01",
        [10.0, 20.0, 40.0, 50.0, 80.0],
        [0.1, 0.2, 0.4, 0.5, 0.8],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "image_processing_derived_column"


def test_prefilter_does_not_treat_time_minutes_as_image_minimum():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "20 min",
        "60 min",
        20,
        1.0,
        "col[5] = col[3] + -0.401384",
        [0.81, 0.72, 0.69, 0.63, 0.51],
        [0.408616, 0.318616, 0.288616, 0.228616, 0.108616],
    )

    assert f["flags"]["image_derived_label"] is False
    assert f["prefilter"] == "keep"


def test_prefilter_does_not_drop_plain_slash_ratio_labels_as_image_processing():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "R",
        "R / R0",
        40,
        1.0,
        "col[2] = col[1] * 0.5",
        [2.1, 4.4, 7.8, 6.2, 3.3],
        [1.05, 2.2, 3.9, 3.1, 1.65],
    )

    assert f["flags"]["image_derived_label"] is False
    assert f["prefilter"] == "keep"


def test_prefilter_does_not_drop_slash_condition_labels_as_image_processing():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "identical_column",
        "SuperFi-Cas9/21-sgRNA",
        "SuperFi-Cas9/22-sgRNA",
        8,
        1.0,
        "col[2] == col[1]",
        [0.12, 0.34, 0.56, 0.78, 0.91],
        [0.12, 0.34, 0.56, 0.78, 0.91],
    )

    assert f["flags"]["image_derived_label"] is False
    assert f["prefilter"] == "keep"


def test_prefilter_keeps_independent_curve_offset_candidate():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "0V-(1)",
        "0V-(5)",
        2000,
        1.0,
        "col[13] = col[1] + -1.2",
        [2.1, 4.4, 7.8, 6.2, 3.3],
        [0.9, 3.2, 6.6, 5.0, 2.1],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_drops_common_unit_scale_even_with_blank_labels():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "",
        "",
        20,
        1.0,
        "col[13] = col[12] * 0.001",
        [363.86, 97.52, 89.44, 286.53, 95.41],
        [0.36386, 0.09752, 0.08944, 0.28653, 0.09541],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "unit_conversion_or_normalization"


def test_prefilter_drops_genome_size_pg_to_mbp_conversion():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "1C_pg",
        "1C_Mbp",
        42,
        1.0,
        "col[5] = col[4] * 980",
        [4.43, 4.70, 6.00, 7.35, 8.28],
        [4341.4, 4606.0, 5880.0, 7203.0, 8114.4],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "unit_conversion_or_normalization"


def test_prefilter_does_not_treat_pg_to_gbp_factor_as_mbp_conversion():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "1C_pg",
        "1C_Mbp",
        42,
        1.0,
        "col[7] = col[4] * 0.98",
        [4.43, 4.70, 6.00, 7.35, 8.28],
        [4.3414, 4.606, 5.88, 7.203, 8.1144],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_drops_genome_size_pg_to_gbp_conversion():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "1C_pg",
        "1C_Gbp",
        42,
        1.0,
        "col[7] = col[4] * 0.98",
        [4.43, 4.70, 6.00, 7.35, 8.28],
        [4.3414, 4.606, 5.88, 7.203, 8.1144],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "unit_conversion_or_normalization"


def test_prefilter_keeps_unlabeled_980_factor_without_genome_size_context():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "",
        "",
        23,
        1.0,
        "col[5] = col[4] * 980",
        [0.63, 0.93, 1.05, 1.27, 1.50],
        [617.4, 911.4, 1029.0, 1244.6, 1470.0],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_keeps_non_genome_size_text_labels_with_980_ratio():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "Drug A response",
        "Drug B response",
        12,
        1.0,
        "col[2] = col[1] * 980",
        [0.63, 0.93, 1.05, 1.27, 1.50],
        [617.4, 911.4, 1029.0, 1244.6, 1470.0],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_keeps_non_unit_tiny_ratio():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "",
        "",
        8,
        1.0,
        "col[9] = col[3] * 1.27648e-09",
        [1.0e7, 2.2e7, 3.1e7, 4.8e7, 5.6e7],
        [0.0127648, 0.02808256, 0.03957088, 0.06127104, 0.07148288],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_keeps_non_unit_ratio_between_labeled_conditions():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "Control",
        "Treatment",
        20,
        1.0,
        "col[2] = col[1] * 2.37",
        [1.0, 2.1, 3.7, 5.2, 8.8],
        [2.37, 4.977, 8.769, 12.324, 20.856],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_does_not_auto_drop_pvalue_qvalue_pair_that_may_be_independent_statistics():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "identical_column",
        "FDR q-value",
        "FUSION P-value",
        21,
        1.0,
        "col[6] == col[5]",
        [0.0012, 0.004, 0.031, 0.2, 0.8],
        [0.0012, 0.004, 0.031, 0.2, 0.8],
    )

    assert f["prefilter"] == "downweight"
    assert f["prefilter_reason"] == "derived_statistical_column"


def test_prefilter_drops_genomic_start_end_coordinate_table():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "Start",
        "End",
        1000,
        1.0,
        "col[2] = col[1] + 501",
        [86239102.0, 86242935.0, 86243600.0, 86257422.0, 86258510.0],
        [86239603.0, 86243436.0, 86244101.0, 86257923.0, 86259011.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "genomic_coordinate_table"


def test_prefilter_does_not_treat_small_start_end_values_as_genomic_coordinates():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "Start",
        "End",
        5,
        1.0,
        "col[2] = col[1] + 2",
        [1.0, 4.0, 8.0, 15.0, 16.0],
        [3.0, 6.0, 10.0, 17.0, 18.0],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_drops_explicit_multiplier_formula_labels():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "Int",
        "Int. x10000",
        30,
        1.0,
        "col[2] = col[1] * 10000",
        [0.12, 0.34, 0.56, 0.78, 0.91],
        [1200.0, 3400.0, 5600.0, 7800.0, 9100.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "explicit_formula_or_unit_conversion"


def test_prefilter_does_not_treat_coordinate_x1_y1_as_formula():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "x1",
        "y1",
        4101,
        1.0,
        "col[2] = col[1] + 10000",
        [1.31, 2.47, 2.93, 4.81, 7.29],
        [10001.31, 10002.47, 10002.93, 10004.81, 10007.29],
    )

    assert f["flags"]["explicit_formula_label"] is False
    assert f["prefilter"] == "keep"


def test_prefilter_keeps_compact_coordinate_x0_x2_labels():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "identical_column",
        "x0",
        "x2",
        5,
        1.0,
        "col[20] == col[18]",
        [0.11, 0.29, 0.37, 0.68, 0.91],
        [0.11, 0.29, 0.37, 0.68, 0.91],
    )

    assert f["flags"]["explicit_formula_label"] is False
    assert f["prefilter"] == "keep"


def test_prefilter_does_not_treat_sample_size_slash_labels_as_formula():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "34,990 cases / 150,760 controls",
        "17,790 cases / 243,645 controls",
        20,
        1.0,
        "col[5] = 1.26 * col[4] + -1065",
        [112.0, 245.0, 301.0, 499.0, 812.0],
        [-923.88, -756.3, -685.74, -436.26, -41.88],
    )

    assert f["flags"]["explicit_formula_label"] is False
    assert f["prefilter"] == "keep"


def test_prefilter_reports_formula_reason_before_axis_when_formula_samples_are_arithmetic():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "Int",
        "Int. x10000",
        30,
        1.0,
        "col[2] = col[1] * 10000",
        [0.1, 0.2, 0.3, 0.4, 0.5],
        [1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "explicit_formula_or_unit_conversion"


def test_prefilter_drops_count_to_probability_fixed_denominator():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "nDots",
        "probability",
        21,
        1.0,
        "col[2] = col[1] / 21",
        [1.0, 2.0, 5.0, 8.0, 13.0],
        [1.0 / 21.0, 2.0 / 21.0, 5.0 / 21.0, 8.0 / 21.0, 13.0 / 21.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "count_to_probability_or_rate"


def test_prefilter_drops_read_count_to_coverage_or_mapping_rate():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "read count",
        "coverage",
        100,
        1.0,
        "col[2] = col[1] * 0.001",
        [1000.0, 2500.0, 8000.0, 13000.0, 21000.0],
        [1.0, 2.5, 8.0, 13.0, 21.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "count_to_probability_or_rate"


def test_prefilter_drops_target_start_end_coordinate_pairs_even_when_small():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "Target-start",
        "Target-end",
        30,
        1.0,
        "col[2] = col[1] + 20",
        [1.0, 30.0, 55.0, 101.0, 150.0],
        [21.0, 50.0, 75.0, 121.0, 170.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "genomic_coordinate_table"


def test_prefilter_drops_common_complement_category_labels():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "sum_constant",
        "With tumors",
        "Without tumors",
        24,
        1.0,
        "col[1] + col[2] = 12",
        [1.0, 2.0, 3.0, 5.0, 8.0],
        [11.0, 10.0, 9.0, 7.0, 4.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "complement_category_sum_to_constant"


def test_prefilter_keeps_large_raw_count_replicate_duplicate():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "identical_column",
        "AGO2 MO2_I",
        "AGO2 MO2_II",
        25729,
        1.0,
        "col[2] == col[1]",
        [0.0, 12.0, 33.0, 5.0, 100.0],
        [0.0, 12.0, 33.0, 5.0, 100.0],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_downweights_benjamini_and_bonferroni_statistic_columns():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "Rank",
        "Critical Benjamini",
        30,
        1.0,
        "col[2] = col[1] * 0.05",
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [0.05, 0.1, 0.15, 0.2, 0.25],
    )

    assert f["prefilter"] == "downweight"
    assert f["prefilter_reason"] == "derived_statistical_column"


def test_prefilter_downweights_low_information_sparse_transform():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "",
        "",
        6,
        1.0,
        "col[2] = col[1] * 2",
        [0.0, 0.0, 1.0, 0.0, 1.0],
        [0.0, 0.0, 2.0, 0.0, 2.0],
    )

    assert f["prefilter"] == "downweight"
    assert f["prefilter_reason"] == "low_information_sparse_transform"


def test_prefilter_keeps_small_high_precision_independent_transform():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "Spt5-WT",
        "Spt5-M5",
        6,
        1.0,
        "col[2] = col[1] + 49",
        [12.31, 14.22, 16.98, 18.45, 20.11],
        [61.31, 63.22, 65.98, 67.45, 69.11],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_keeps_independent_conditions_that_sum_to_one():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "sum_constant",
        "shControl",
        "shPACSIN2-D11",
        20,
        1.0,
        "col[4] + col[5] = 1",
        [1.0, 1.0, 0.8889, 0.8889, 0.5556],
        [0.0, 0.0, 0.1111, 0.1111, 0.4444],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_keeps_independent_repeats_that_sum_to_two():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "sum_constant",
        "Repeat1",
        "Repeat2",
        10,
        1.0,
        "col[6] + col[7] = 2",
        [1.109006, 0.830224, 1.32632, 1.139431, 1.223372],
        [0.890994, 1.169776, 0.67368, 0.860569, 0.776628],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_drops_exact_linear_percent_complements():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "GPR15-",
        "GPR15+",
        10,
        1.0,
        "col[2] = -1 * col[1] + 100",
        [80.0, 72.0, 61.5, 54.0, 43.2],
        [20.0, 28.0, 38.5, 46.0, 56.8],
        slope=-1.0,
        intercept=100.0,
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "complement_percentage_sum_to_100"


def test_prefilter_downweights_exact_linear_percent_columns_that_do_not_complement():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "percent viability day 1",
        "percent viability day 2",
        20,
        1.0,
        "col[2] = 0.8 * col[1] + 5",
        [80.0, 72.0, 61.5, 54.0, 43.2],
        [69.0, 62.6, 54.2, 48.2, 39.56],
        slope=0.8,
        intercept=5.0,
    )

    assert f["prefilter"] == "downweight"
    assert f["prefilter_reason"] == "derived_normalized"


def test_prefilter_does_not_drop_exact_linear_complement_from_sample_only():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "exact_linear",
        "GPR15-",
        "GPR15+",
        100,
        1.0,
        "col[2] = -0.8 * col[1] + 84",
        [80.0, 72.0, 61.5, 54.0, 43.2],
        [20.0, 28.0, 38.5, 46.0, 56.8],
        slope=-0.8,
        intercept=84.0,
    )

    assert f["prefilter"] == "keep"


def test_prefilter_drops_signed_formula_counterpart_labels():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "A/r^2",
        "-A/r^2",
        304,
        1.0,
        "col[8] = col[7] * -1",
        [5.31488, 4.74074, 4.25485, 3.84, 3.48299],
        [-5.31488, -4.74074, -4.25485, -3.84, -3.48299],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "explicit_formula_or_unit_conversion"


def test_prefilter_keeps_signed_parenthetical_condition_labels():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_ratio",
        "Signal (baseline)",
        "-Signal (baseline)",
        30,
        1.0,
        "col[2] = col[1] * -1",
        [1.2, 2.5, 3.1, 5.8, 6.4],
        [-1.2, -2.5, -3.1, -5.8, -6.4],
    )

    assert f["prefilter"] == "keep"


def test_prefilter_drops_left_right_genomic_bin_coordinates():
    cp = _collector()
    f = cp.prefilter_relation_finding(
        "constant_offset",
        "startL",
        "endL",
        1165,
        1.0,
        "col[2] = col[1] + 10000",
        [74520000.0, 18780000.0, 13710000.0, 18780000.0, 18730000.0],
        [74530000.0, 18790000.0, 13720000.0, 18790000.0, 18740000.0],
    )

    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "genomic_coordinate_table"
