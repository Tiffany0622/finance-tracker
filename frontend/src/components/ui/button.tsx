// shadcn/ui button pattern, with local visual tokens.
import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
const variants = cva('button disabled:pointer-events-none focus-visible:ring-2 focus-visible:ring-offset-2', {variants: {variant: {default: 'button-primary', secondary: 'button-secondary', danger: 'button-danger'}}, defaultVariants: {variant: 'default'}});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof variants> { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({className, variant, asChild, ...props}, ref) => {
  const Comp = asChild ? Slot : 'button';
  return <Comp ref={ref} className={twMerge(clsx(variants({variant}), className))} {...props}/>;
});
Button.displayName = 'Button';
