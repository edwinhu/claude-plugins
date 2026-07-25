/* merge_panel.sas — Merge meetings + inst_own + index_own, compute pivotalness
 *
 * Prereqs: autoexec.sas loaded (provides libnames: out)
 *          out.meetings (from build_meetings.sas)
 *          out.inst_own (from build_inst_own.sas)
 *          out.mf_own_* (from tfn_holdings_parallel.sas, concatenated below)
 *          ~/sas/MERGE_ASOF.sas
 *
 *          out.npx_cells_YYYY (from build_npx.sas via run_npx_array.sh)
 *          out.npx_items      (from stage_npx_link.sas — the shared item frame)
 *
 * Output:  out.pass      grain = itemonagendaid  (item-level ownership panel)
 *          out.pass_npx  grain = (itemonagendaid, block)  <- ANALYSIS-READY
 *
 * out.pass_npx is the file the downstream analysis consumes: the ownership panel
 * with each item's per-block observed For/Against/Abstain split attached. It is
 * produced HERE, on the grid, rather than by joining two downloads locally —
 * shipping the joined result is the entire point of the exercise.
 */

%include "pipeline_config.sas";

/* --- Concatenate parallel MF ownership outputs --- */
data index_own;
    set out.mf_own_:;
run;

proc sort data=index_own nodupkey; by qtr permno; run;

/* Rename to match original variable names */
data out.index_own;
    set index_own;
    rqdate = qtr;
    format rqdate yymmddn8.;
    drop qtr;
run;

proc sort data=out.index_own nodupkey; by rqdate permno; run;

/* --- Sort inputs for MERGE_ASOF --- */
proc sort data=out.inst_own;  by permno rdate;                     run;
proc sort data=out.index_own; by permno rqdate;                    run;
proc sort data=out.meetings;  by permno recorddate meetingdate;    run;

/* --- Create merge views with transformations --- */
data _meetings / view=_meetings;
    set out.meetings;
run;

data _inst_own / view=_inst_own;
    set out.inst_own;
    recorddate = rdate;
    if IOR > 1.2 then delete;
    IOR = min(max(IOR,0.0),1.0)*100;
run;

data _index_own / view=_index_own;
    set out.index_own;
    recorddate = rqdate;
    MF_PCT = 100 * MF_PCT;
    PASSIVE_PCT = 100 * PASSIVE_PCT;
    INDEX_PCT = 100 * INDEX_PCT;
run;

/* --- As-of merges --- */
%INCLUDE "~/sas/MERGE_ASOF.sas";

%MERGE_ASOF(a=_meetings, b=_inst_own,
    merged=pass1,
    idvar=permno,
    datevar=recorddate,
    num_vars=numowners io_total ioc_hhi me ior);

%MERGE_ASOF(a=pass1, b=_index_own,
    merged=pass2,
    idvar=permno,
    datevar=recorddate,
    num_vars=num_mf_owners mf_total passive_total pure_index_total mf_pct passive_pct index_pct);

/* --- Pivotalness indicators --- */
data out.pass;
    set pass2;
    inst_pivotal = abs(forpct-50) <= ior;
    mf_pivotal = abs(forpct-50) <= mf_pct;
    passive_pivotal = abs(forpct-50) <= passive_pct;
    index_pivotal = abs(forpct-50) <= index_pct;
run;

proc contents data=out.pass order=varnum; run;

proc sql;
    select count(*) as nobs format comma12.,
           count(distinct permno) as n_permnos format comma12.
    from out.pass;
quit;

%put NOTE: merge_panel complete;


/* ==========================================================================
 * N-PX leg: assert one universe, then join the cells onto the panel
 * ========================================================================== */

/* --- Stack the per-year cell datasets --------------------------------------
 * Tasks partition on npx.meetingdate and a handful of itemonagendaids carry
 * rows in more than one meeting year (restated filings), so their cells arrive
 * split across two task outputs. SUM them; a plain concat would leave duplicate
 * keys in a dataset whose stated grain is unique. */
data npx_cells_raw;
    set out.npx_cells_:;
run;

proc summary data=npx_cells_raw nway missing;
    class itemonagendaid block;
    var n_rows n_for n_against n_abstain n_other
        sv_for sv_against sv_abstain n_no_sv
        tna_for tna_against tna_abstain n_no_tna;
    output out=out.npx_cells(drop=_type_ _freq_) sum=;
run;

/* --- ASSERT ONE UNIVERSE ---------------------------------------------------
 * out.meetings is built by the ownership leg; out.npx_items by the N-PX leg.
 * Both derive from pipeline_config.sas, so every meetings item MUST exist in
 * the item frame. If one does not, the two legs were built over different
 * windows or filters and the panel is silently wrong — FAIL, do not warn.
 *
 * The reverse is expected and fine: build_meetings additionally drops records
 * with an unusable `base`, turnout > 120, and votedFor <= 0 with 'Pass', so
 * npx_items is a superset. That difference is reported, not asserted.
 */
proc sql noprint;
    create table _orphan_items as
        select a.itemonagendaid
        from out.meetings a
        left join out.npx_items b on a.itemonagendaid = b.itemonagendaid
        where b.itemonagendaid is null;
    select count(*) into :n_orphan trimmed from _orphan_items;
    select count(distinct itemonagendaid) into :n_meet  trimmed from out.meetings;
    select count(*)                       into :n_items trimmed from out.npx_items;
quit;

%put NOTE: UNIVERSE meetings_items=&n_meet. npx_items=&n_items. orphans=&n_orphan.;

data _null_;
    if &n_orphan. > 0 then do;
        put "ERROR: UNIVERSE MISMATCH — &n_orphan. item(s) in out.meetings are absent";
        put "ERROR- from out.npx_items. The two legs were built over different";
        put "ERROR- universes. Both must read pipeline_config.sas; check that no";
        put "ERROR- script re-declares year1/year2/MEETINGTYPES/VOTERESULTS.";
        abort abend;
    end;
run;

/* --- The join --------------------------------------------------------------
 * LEFT join from the panel: every panel item is kept, whether or not any fund
 * disclosed a vote on it. Items with no N-PX coverage get a single row with a
 * null block, so their absence is visible rather than silently dropped. */
proc sql;
    create table out.pass_npx as
        select a.*,
               b.block,
               b.n_rows, b.n_for, b.n_against, b.n_abstain, b.n_other,
               b.sv_for, b.sv_against, b.sv_abstain, b.n_no_sv,
               b.tna_for, b.tna_against, b.tna_abstain, b.n_no_tna
        from out.pass a
        left join out.npx_cells b
          on a.itemonagendaid = b.itemonagendaid
        order by a.itemonagendaid, b.block;
quit;

/* Directional shares. Denominator is For+Against+Abstain — `n_other` (frequency
 * votes, proxy-contest card labels) is deliberately EXCLUDED from the share but
 * retained as a column, so a ballot that is mostly 'Other' is visible as a low
 * usable count rather than as a confident-looking split. */
data out.pass_npx;
    set out.pass_npx;
    n_usable = sum(n_for, n_against, n_abstain);
    if n_usable > 0 then do;
        for_frac     = n_for     / n_usable;
        against_frac = n_against / n_usable;
        abstain_frac = n_abstain / n_usable;
    end;
    /* Weighted shares are only meaningful where the weight exists at all:
     * sharesvoted is ~0% populated before 2023, and tna only covers funds the
     * crosswalk linked. n_no_sv / n_no_tna carry the size of each residual. */
    sv_total  = sum(sv_for, sv_against, sv_abstain);
    tna_total = sum(tna_for, tna_against, tna_abstain);
    if sv_total  > 0 then sv_for_frac  = sv_for  / sv_total;
    if tna_total > 0 then tna_for_frac = tna_for / tna_total;
run;

proc sql;
    select count(*)                        as n_rows        format=comma14.,
           count(distinct itemonagendaid)  as n_items       format=comma14.,
           sum(block is null)              as items_no_npx  format=comma14.,
           sum(n_rows)                     as vote_rows     format=comma18.
    from out.pass_npx;

    select block,
           count(*)    as cells     format=comma12.,
           sum(n_rows) as vote_rows format=comma18.
    from out.pass_npx where block is not null
    group by block order by vote_rows desc;
quit;

%put NOTE: merge_panel complete — out.pass (item grain), out.pass_npx (item x block);
