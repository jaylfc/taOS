# RED Proof for Storage Engine Implementation

## Failing Tests Before Implementation

```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: /tmp/exec-tsk-v4vecx
collected 34 items

tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_available_clean_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_system_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_system_disk_by_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_read_only FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_mounted_whole_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_mounted_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_has_data_fs_on_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_has_data_fs_on_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_encrypted_foreign_luks_on_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_encrypted_foreign_luks_on_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_raid_member_on_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_raid_member_on_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_lvm_member_on_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_lvm_member_on_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_zfs_member_on_disk FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_zfs_member_on_partition FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_multiple_reasons_combined FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_swap_not_protected_as_data FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_empty_partition_table_not_protected FAILED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_virtual_devices_filtered_by_discover FAILED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_success FAILED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_success_no_encrypt FAILED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_refuses_mismatched_confirm FAILED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_refuses_unavailable_disk FAILED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_refuses_disk_that_gained_data FAILED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_helper_failure_rollback FAILED
tests/test_storage_engine_integration.py::TestSetPrimaryHome::test_set_primary_home_success FAILED
tests/test_storage_engine_integration.py::TestSetPrimaryHome::test_set_primary_home_area_not_found FAILED
tests/test_storage_engine_integration.py::TestSetPrimaryHome::test_set_primary_home_person_not_found FAILED
tests/test_storage_engine_integration.py::TestListStorageAreas::test_list_storage_areas FAILED
tests/test_storage_engine_integration.py::TestGetPrimaryHomeCapacity::test_get_primary_home_capacity FAILED
tests/test_storage_engine_integration.py::TestGetPrimaryHomeCapacity::test_get_primary_home_capacity_no_primary FAILED
tests/test_storage_engine_integration.py::TestGetPrimaryHomeCapacity::test_get_primary_home_capacity_person_not_found FAILED
tests/test_storage_engine_integration.py::TestDiscoverDisks::test_discover_disks_mocked FAILED

=========================== 34 failed in 0.10s ===============================
```

## All Tests Passing After Implementation

```
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: /tmp/exec-tsk-v4vecx
collected 34 items

tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_available_clean_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_system_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_system_disk_by_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_read_only PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_mounted_whole_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_mounted_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_has_data_fs_on_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_has_data_fs_on_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_encrypted_foreign_luks_on_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_encrypted_foreign_luks_on_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_raid_member_on_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_raid_member_on_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_lvm_member_on_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_lvm_member_on_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_zfs_member_on_disk PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_protected_zfs_member_on_partition PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_multiple_reasons_combined PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_swap_not_protected_as_data PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_empty_partition_table_not_protected PASSED
tests/test_storage_safety_classifier.py::TestDiskSafetyClassifier::test_virtual_devices_filtered_by_discover PASSED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_success PASSED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_success_no_encrypt PASSED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_refuses_mismatched_confirm PASSED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_refuses_unavailable_disk PASSED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_refuses_disk_that_gained_data PASSED
tests/test_storage_engine_integration.py::TestAddStorageArea::test_add_storage_area_helper_failure_rollback PASSED
tests/test_storage_engine_integration.py::TestSetPrimaryHome::test_set_primary_home_success PASSED
tests/test_storage_engine_integration.py::TestSetPrimaryHome::test_set_primary_home_area_not_found PASSED
tests/test_storage_engine_integration.py::TestSetPrimaryHome::test_set_primary_home_person_not_found PASSED
tests/test_storage_engine_integration.py::TestListStorageAreas::test_list_storage_areas PASSED
tests/test_storage_engine_integration.py::TestGetPrimaryHomeCapacity::test_get_primary_home_capacity PASSED
tests/test_storage_engine_integration.py::TestGetPrimaryHomeCapacity::test_get_primary_home_capacity_no_primary PASSED
tests/test_storage_engine_integration.py::TestGetPrimaryHomeCapacity::test_get_primary_home_capacity_person_not_found PASSED
tests/test_storage_engine_integration.py::TestDiscoverDisks::test_discover_disks_mocked PASSED

=========================== 34 passed in 0.63s ===============================
```