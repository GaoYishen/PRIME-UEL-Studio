! Standalone elastic spring workflow example. This is not a battery UEL.
program spring_example
  use, intrinsic :: iso_fortran_env, only: real64, error_unit
  use, intrinsic :: ieee_arithmetic, only: ieee_is_finite
  implicit none
  integer :: input, history, metrics, index, steps, ios
  real(real64) :: stiffness, initial_mm, final_mm, duration
  real(real64) :: time, fraction, displacement_mm, displacement_m
  real(real64) :: force, energy, probe, perturbation, numerical, error

  open(newunit=input, file='parameters.dat', status='old', action='read', iostat=ios)
  if (ios /= 0) error stop 'Cannot open parameters.dat'
  read(input, *, iostat=ios) stiffness
  if (ios /= 0) error stop 'Invalid stiffness'
  read(input, *, iostat=ios) initial_mm
  if (ios /= 0) error stop 'Invalid initial displacement'
  read(input, *, iostat=ios) final_mm
  if (ios /= 0) error stop 'Invalid final displacement'
  read(input, *, iostat=ios) duration
  if (ios /= 0) error stop 'Invalid duration'
  read(input, *, iostat=ios) steps
  if (ios /= 0) error stop 'Invalid step count'
  close(input)
  if (.not. all(ieee_is_finite([stiffness,initial_mm,final_mm,duration]))) error stop 'Non-finite input'
  if (stiffness <= 0.0_real64 .or. duration <= 0.0_real64) error stop 'Stiffness and duration must be positive'
  if (steps < 2 .or. steps > 1000) error stop 'Step count outside [2,1000]'

  open(newunit=history, file='history.csv', status='new', action='write')
  write(history, '(A)') 'time_s,displacement_mm,force_N,energy_J'
  do index=0,steps
    fraction=real(index,real64)/real(steps,real64)
    time=duration*fraction
    displacement_mm=initial_mm+(final_mm-initial_mm)*fraction
    displacement_m=displacement_mm*1.0e-3_real64
    force=stiffness*displacement_m
    energy=0.5_real64*stiffness*displacement_m*displacement_m
    write(history, '(ES24.16E3,3(",",ES24.16E3))') time,displacement_mm,force,energy
  end do
  close(history)

  probe=0.5_real64*(initial_mm+final_mm)*1.0e-3_real64
  perturbation=1.0e-7_real64
  numerical=(stiffness*(probe+perturbation)-stiffness*(probe-perturbation))/(2.0_real64*perturbation)
  error=abs(numerical-stiffness)/stiffness
  open(newunit=metrics, file='metrics.csv', status='new', action='write')
  write(metrics, '(A)') 'relative_tangent_error'
  write(metrics, '(ES24.16E3)') error
  close(metrics)
  write(*, '(A,I0,A,ES12.4)') 'Spring example completed: ',steps+1,' frames; relative tangent error=',error
end program spring_example
