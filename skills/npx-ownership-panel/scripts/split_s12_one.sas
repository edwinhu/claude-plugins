/* split_s12_one.sas — ONE S12 partition per SGE task. Replaces the sequential
 * %extract_all loop in split_s12.sas.
 *
 * Prereqs: autoexec.sas (libname out), ~/.pgpass
 * Usage:   run_s12_array.sh (qsub -t 1-N -tc 6), sysparm = "YYYY-YYYY"
 *
 * WHY THIS STILL GOES THROUGH POSTGRESQL
 * --------------------------------------
 * Every other source table in this pipeline is read natively from its SAS
 * libname, because each carries a SAS index that prunes a where= to one task's
 * slice. Whether tfn.s12 should join them is a MEASURED question — see
 * references/pipeline.md, "The S12 read path". Do not change it on reasoning.
 *
 * WHY -tc MATTERS: each task opens its own PostgreSQL connection, and the WRDS
 * per-role cap is 7 (`select rolconnlimit from pg_roles where rolname =
 * current_user`). Nine concurrent tasks exceed it. A refused connection is not
 * reliably a loud error, so the failure mode is a SILENTLY MISSING PARTITION —
 * which is why run_s12_array.sh sets -tc and merge_panel.sas counts partitions.
 */

%include "pipeline_config.sas";

%let range = &sysparm.;
%let y1 = %scan(&range., 1, -);
%let y2 = %scan(&range., 2, -);

%put NOTE: split_s12_one range=&y1.-&y2. started at %sysfunc(datetime(),datetime19.);

filename pgf '~/.pgpass';
data _null_;
    infile pgf truncover;
    input line $200.;
    if index(line, 'wrds-pgdata') > 0 then call symputx('pgpw', scan(line, 5, ':'), 'G');
run;

proc sql;
    connect to postgres (server='wrds-pgdata-ident-w.wharton.private'
                         port=9737 user=&sysuserid. password="&pgpw." database=wrds);
    create table out.s12_&y1._&y2. as
        select * from connection to postgres (
            select fdate, fundname, fundno, rdate, cusip, shares
            from tr_mutualfunds.s12
            where rdate >= %str(%')&y1.-01-01%str(%')
              and rdate <= %str(%')&y2.-12-31%str(%')
              and shares > 0
        );
    disconnect from postgres;
quit;

%macro _s12_check;
    %if not %sysfunc(exist(out.s12_&y1._&y2.)) %then %do;
        %put ERROR: S12PART range=&y1.-&y2. NOT CREATED — likely a refused PG;
        %put ERROR- connection (per-role cap is 7). Lower -tc and re-run this task.;
        data _null_; abort abend; run;
    %end;
%mend;
%_s12_check

proc sql noprint;
    select count(*) into :cnt trimmed from out.s12_&y1._&y2.;
quit;
%put NOTE: S12PART range=&y1.-&y2. rows=&cnt.;
%put NOTE: split_s12_one complete at %sysfunc(datetime(),datetime19.);
