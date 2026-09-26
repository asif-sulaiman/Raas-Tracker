"""Compatibility shim: implementation lives in the raas_tracker/ package.

New code should import from raas_tracker.db, raas_tracker.audit, raas_tracker.auth,
raas_tracker.stock, raas_tracker.uploads, raas_tracker.recipes, or raas_tracker.sales directly.
"""

from raas_tracker.db import (
    DB_PATH,
    JSON_PATH,
    _create_tables,
    get_connection,
    logger,
)

from raas_tracker.audit import (
    _audit_state,
    get_audit_logs,
    log_audit_action,
    set_audit_actor,
)

from raas_tracker.auth import (
    BCRYPT_ROUNDS,
    LOGIN_WINDOW_MINUTES,
    MAX_LOGIN_FAILS,
    SESSION_TTL_HOURS,
    _DUMMY_HASH,
    _api_key_hash,
    _check_password,
    _dummy_hash,
    _hash_password,
    _ip_allowed,
    check_api_key_rate_limit,
    check_setup_token,
    cleanup_expired_sessions,
    count_users,
    create_api_key,
    create_first_admin,
    create_session,
    create_user,
    delete_user,
    ensure_setup_token,
    get_session_user,
    get_setting,
    get_user_by_username,
    is_login_blocked,
    list_api_keys,
    list_users,
    record_api_key_hit,
    record_login_attempt,
    revoke_api_key,
    revoke_session,
    revoke_user_sessions,
    validate_api_key,
    validate_password,
    validate_username,
    verify_user,
)

from raas_tracker.stock import (
    add_chemical,
    add_unit_conversion,
    convert_quantity,
    delete_unit_conversion,
    get_all_chemicals,
    get_all_unit_conversions,
    get_stock_movements,
    get_unit_conversion,
    import_from_json,
    set_reorder_level,
    update_stock,
)

from raas_tracker.uploads import (
    adjust_stock_from_upload,
    approve_upload,
    approve_upload_row,
    compare_stock_upload,
    create_approval_workflow,
    create_reconciliation_period,
    csv_safe,
    export_comparison_report,
    get_all_reason_codes,
    get_pending_approvals,
    get_unmapped_rows,
    get_upload_history,
    get_upload_results,
    lock_reconciliation_period,
    reject_upload_row,
    save_upload,
    validate_expiry_date,
)

from raas_tracker.recipes import (
    add_recipe,
    add_recipe_item,
    delete_recipe,
    delete_recipe_item,
    export_report_to_csv,
    generate_multi_recipe_report,
    generate_report,
    get_recipe_by_name,
    list_chemicals_cli,
    list_recipe_items,
    list_recipes,
    print_report,
    update_recipe,
    update_recipe_item,
)

from raas_tracker.sales import (
    SALE_STAGE_ORDER,
    _complete_if_paid,
    _next_stage,
    _revert_if_unpaid,
    _sync_sale_payment_totals,
    add_sale,
    add_sale_item,
    advance_sale,
    delete_sale,
    delete_sale_item,
    delete_sale_payment_record,
    get_all_sales,
    get_sale_by_id,
    get_sale_invoice_total,
    get_sale_total_paid,
    get_sales_summary,
    move_sale_to_stage,
    record_sale_payment,
    update_sale_full,
    update_sale_item,
    update_sale_lc,
    update_sale_payment,
    update_sale_payment_record,
)

from raas_tracker.cli import main


if __name__ == "__main__":
    main()
