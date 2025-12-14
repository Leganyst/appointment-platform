from aiogram.fsm.state import State, StatesGroup


class ClientStates(StatesGroup):
    welcome = State()           # S0
    role_setup = State()        # S1
    main_menu = State()         # S2
    service_search = State()    # S3
    slots_view = State()        # S4
    booking_confirm = State()   # S5
    booking_result = State()    # S6
    my_bookings = State()       # S7
    booking_details = State()   # S8
    cancel_result = State()     # S9
    profile_help = State()      # S10
