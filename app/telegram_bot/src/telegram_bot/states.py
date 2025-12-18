from aiogram.fsm.state import State, StatesGroup


class ClientStates(StatesGroup):
    welcome = State()           # S0
    role_setup = State()        # S1
    role_setup_contact = State()
    role_setup_confirm = State()
    main_menu = State()         # S2
    service_search = State()    # S3
    provider_phone_search = State()  # S3.1
    slots_view = State()        # S4
    booking_confirm = State()   # S5
    booking_result = State()    # S6
    my_bookings = State()       # S7
    booking_details = State()   # S8
    cancel_result = State()     # S9
    profile_help = State()      # S10


class ProviderStates(StatesGroup):
    welcome = State()           # P0
    role_setup = State()        # P1 (legacy single-step)
    role_setup_name = State()
    role_setup_description = State()
    role_setup_contact = State()
    role_setup_confirm = State()
    main_menu = State()         # P2
    schedule_dashboard = State()  # P3
    slot_create_service = State()  # P3.1
    slot_create_date = State()     # P4.0
    slot_create_time = State()     # P4.1
    slot_create_duration = State() # P4.2
    slot_create = State()         # P4.3 confirm
    slot_edit = State()           # P5
    slot_delete = State()         # P6
    booking_list = State()        # P7
    booking_confirm = State()     # P8
    booking_cancel = State()      # P9
    profile_help = State()        # P10
